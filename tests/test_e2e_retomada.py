#!/usr/bin/env python3
"""
Teste end-to-end: executa o CLI de verdade contra um PostgreSQL real e
verifica estado, retomada, --force, webhooks, dashboard e pipeline_stats
funcionando em conjunto.

Rodar: python3 tests/test_e2e_retomada.py

Usa `db init`, que cria o schema completo e carrega as tabelas do IBGE — é a
etapa real mais barata do pipeline, e exercita o caminho inteiro
(main -> orchestrator -> run_step -> estado -> stats).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import psycopg2  # noqa: E402

PASS = FAIL = 0
CONTAINER = "cnpj-e2e-test"
SENHA = "teste-local"
PERIODO = "2026-07"


def check(nome, condicao, detalhe=""):
    global PASS, FAIL
    if condicao:
        PASS += 1
        print(f"  ✓ {nome}")
    else:
        FAIL += 1
        print(f"  ✗ {nome} {detalhe}")


def subir_postgres():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER,
         "-e", f"POSTGRES_PASSWORD={SENHA}", "-e", "POSTGRES_DB=e2e",
         "-P", "postgres:17-alpine"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ! docker run falhou: {r.stderr.strip()}")
        return None
    porta = subprocess.run(["docker", "port", CONTAINER, "5432/tcp"],
                           capture_output=True, text=True).stdout.strip().rsplit(":", 1)[-1]
    for _ in range(60):
        try:
            psycopg2.connect(f"postgresql://postgres:{SENHA}@127.0.0.1:{porta}/e2e").close()
            return porta
        except psycopg2.Error:
            time.sleep(1)
    return None


# ---- coletor de webhooks -----------------------------------------------------
recebidos = []


class _Coletor(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        recebidos.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200); self.send_header("Content-Length", "2")
        self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        return


print("subindo Postgres efêmero…")
porta_pg = subir_postgres()
if not porta_pg:
    print("SKIP: Docker indisponível")
    sys.exit(0)

hook = ThreadingHTTPServer(("127.0.0.1", 0), _Coletor)
threading.Thread(target=hook.serve_forever, daemon=True).start()
porta_hook = hook.server_address[1]

state_dir = Path(tempfile.mkdtemp(prefix="cnpj-e2e-state-"))

env = os.environ.copy()
env.update({
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": porta_pg,
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": SENHA,
    "POSTGRES_DBNAME": "e2e",
    "PIPELINE_STATE_DIR": str(state_dir),
})


def rodar(*extra):
    """Executa o CLI e devolve (returncode, saída combinada)."""
    r = subprocess.run(
        [sys.executable, "etl.py", "db", "init", "--db-name", "e2e",
         "--reference-period", PERIODO, *extra],
        cwd=RAIZ, env=env, capture_output=True, text=True, timeout=300,
    )
    return r.returncode, r.stdout + r.stderr


estado = state_dir / f"pipeline_state_{PERIODO}.json"

try:
    # ---------------------------------------------------------- 1ª execução
    print("\n1ª execução — cria estado, executa etapa, grava stats")
    rc, saida = rodar("--webhook-url", f"http://127.0.0.1:{porta_hook}/hook")
    check("CLI terminou com sucesso", rc == 0, f"\n{saida[-1500:]}")
    check("arquivo de estado foi criado no período correto", estado.exists())

    st = json.loads(estado.read_text())
    check("run_id é UUID", len(st["run_id"]) == 36)
    check("reference_period correto", st["reference_period"] == PERIODO)
    check("created_at tem offset de fuso",
          "+" in st["created_at"][10:] or "-" in st["created_at"][10:])
    schema = next(s for s in st["steps"] if s["name"] == "schema_init")
    check("etapa schema_init concluiu", schema["status"] == "success",
          f"(status: {schema['status']}, erro: {schema.get('error')})")
    check("etapa tem started_at e finished_at",
          schema["started_at"] and schema["finished_at"])
    check("status geral do pipeline", st["status"] == "completed",
          f"(veio: {st['status']})")

    # --- ambiente e banco gravados pelo CLI real
    amb = st.get("environment", {})
    check("estado traz o ambiente de execução", bool(amb.get("hostname")))
    check("identifica runtime (docker/python)", amb.get("runtime") in ("docker", "python"))
    bd = st.get("database", {})
    check("estado traz o banco alvo", bd.get("database") == "e2e")
    check("estado traz a versão do PostgreSQL", "PostgreSQL" in (bd.get("versao") or ""))
    check("estado traz o tamanho do banco", bool(bd.get("tamanho")))
    check("senha do banco não vaza no estado",
          "password" not in json.dumps(bd) and SENHA not in json.dumps(st))

    eventos = [e["event"] for e in recebidos]
    check("webhook pipeline_started recebido", "pipeline_started" in eventos)
    check("webhook step_completed recebido", "step_completed" in eventos)
    check("webhook pipeline_completed recebido", "pipeline_completed" in eventos)

    conn = psycopg2.connect(f"postgresql://postgres:{SENHA}@127.0.0.1:{porta_pg}/e2e")
    with conn.cursor() as cur:
        cur.execute("SELECT run_id::text, status, reference_period, finished_at, tables_populated "
                    "FROM pipeline_stats ORDER BY started_at DESC LIMIT 1;")
        linha = cur.fetchone()
    check("pipeline_stats gravou a execução", linha is not None)
    if linha:
        check("run_id do banco bate com o do JSON", linha[0] == st["run_id"])
        check("status registrado como completed", linha[1] == "completed")
        check("finished_at preenchido", linha[3] is not None)
        check("tables_populated lista as tabelas do IBGE carregadas",
              linha[4] is not None and len(linha[4]) >= 0)

    # ---------------------------------------------------------- 2ª execução
    print("\n2ª execução — deve PULAR a etapa já concluída")
    rc2, saida2 = rodar()
    check("CLI terminou com sucesso", rc2 == 0, f"\n{saida2[-1000:]}")
    check("log indica que a etapa foi pulada",
          "JÁ CONCLUÍDA, PULANDO: schema_init" in saida2,
          f"\n{saida2[-800:]}")
    check("log indica estado encontrado",
          "ESTADO ENCONTRADO" in saida2)

    st2 = json.loads(estado.read_text())
    check("run_id preservado entre execuções", st2["run_id"] == st["run_id"])
    schema2 = next(s for s in st2["steps"] if s["name"] == "schema_init")
    check("etapa pulada mantém attempts=1 (não reexecutou)",
          schema2["attempts"] == 1, f"(veio: {schema2['attempts']})")
    check("finished_at não mudou", schema2["finished_at"] == schema["finished_at"])

    # ------------------------------------------------------- 3ª com --force
    print("\n3ª execução — --force reexecuta do zero")
    rc3, saida3 = rodar("--force")
    check("CLI terminou com sucesso", rc3 == 0, f"\n{saida3[-1000:]}")
    check("log indica backup do estado anterior",
          "ESTADO ANTERIOR PRESERVADO" in saida3)
    backups = list(state_dir.glob(f"pipeline_state_{PERIODO}.json.bak-*"))
    check("backup .bak foi criado", len(backups) == 1,
          f"({[b.name for b in backups]})")
    st3 = json.loads(estado.read_text())
    check("--force gerou novo run_id", st3["run_id"] != st["run_id"])
    schema3 = next(s for s in st3["steps"] if s["name"] == "schema_init")
    check("etapa reexecutou (não foi pulada)", schema3["status"] == "success")
    check("não aparece 'PULANDO' com --force",
          "JÁ CONCLUÍDA, PULANDO" not in saida3)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats;")
        total = cur.fetchone()[0]
    check("pipeline_stats acumula histórico de execuções", total >= 2,
          f"(linhas: {total})")

    # ------------------------------------------------------- 4ª com --no-state
    print("\n4ª execução — --no-state não toca no arquivo")
    antes = estado.read_text()
    rc4, saida4 = rodar("--no-state")
    check("CLI terminou com sucesso", rc4 == 0, f"\n{saida4[-1000:]}")
    check("arquivo de estado não foi alterado", estado.read_text() == antes)
    check("comportamento padrão preservado (sem menção a estado)",
          "ESTADO ENCONTRADO" not in saida4)

    # ---------------------------------------------------------- dashboard
    print("\nDashboard servindo o estado real")
    # --force para o schema ser recriado do zero: uma execução que só pula
    # etapas dura ~1s e fecharia o dashboard antes de qualquer requisição.
    proc = subprocess.Popen(
        [sys.executable, "etl.py", "db", "init", "--db-name", "e2e",
         "--reference-period", PERIODO, "--force", "--serve", "--port", "3117",
         "--dashboard-password", "senha-e2e"],
        cwd=RAIZ, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    import urllib.request, base64
    cred = base64.b64encode(b"pipeline:senha-e2e").decode()

    def _get(rota, autenticado=True):
        req = urllib.request.Request(f"http://127.0.0.1:3117{rota}")
        if autenticado:
            req.add_header("Authorization", "Basic " + cred)
        return urllib.request.urlopen(req, timeout=2)

    html = estado_json = None
    negado = None
    for _ in range(200):
        if proc.poll() is not None:
            break
        try:
            html = _get("/").read().decode()
            estado_json = json.loads(_get("/state.json").read())
            # confirma que sem credencial o servidor recusa
            try:
                _get("/", autenticado=False)
                negado = False
            except urllib.error.HTTPError as e:
                negado = (e.code == 401)
            break
        except Exception:
            time.sleep(0.05)
    check("dashboard exige Basic Auth", negado is True, f"(negado={negado})")
    check("dashboard respondeu durante a execução", html is not None)
    if html:
        check("página tem o título do pipeline", "CNPJ Pipeline" in html)
        check("usa Alpine.js via CDN", "alpinejs" in html)
        check("CSS inline (sem folha de estilo externa)",
              "stylesheet" not in html and "<style>" in html)
        check("polling com intervalo ajustável", "setInterval" in html and "intervalo" in html)
    if estado_json:
        check("dashboard serve o estado do período correto",
              estado_json["reference_period"] == PERIODO)
    proc.wait(timeout=300)
    saida5 = proc.stdout.read()
    check("execução com --serve terminou bem", proc.returncode == 0,
          f"\n{saida5[-800:]}")

    conn.close()

finally:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    hook.shutdown()
    shutil.rmtree(state_dir, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
