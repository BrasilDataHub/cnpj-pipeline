#!/usr/bin/env python3
"""
Testes de estado, retomada, webhooks e dashboard.

Rodar: python3 tests/test_observabilidade.py

Não precisam de banco nem de rede externa: o webhook é apontado para um
servidor HTTP local descartável e o estado usa um diretório temporário.
"""

import json
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rfb_cnpj_etl.utils.run_state import (  # noqa: E402
    RunState, run_step, normalize_reference_period,
    PIPELINE_STEPS, STEP_CARGA, STEP_VIEWS, STATUS_SUCCESS, STATUS_FAILED,
)
from rfb_cnpj_etl.utils.webhook import WebhookNotifier  # noqa: E402
from rfb_cnpj_etl.utils.environment import (  # noqa: E402
    collect_environment, collect_database_info,
)
from rfb_cnpj_etl.utils.dashboard import start_dashboard  # noqa: E402

PASS = FAIL = 0


def check(nome, condicao, detalhe=""):
    global PASS, FAIL
    if condicao:
        PASS += 1
        print(f"  ✓ {nome}")
    else:
        FAIL += 1
        print(f"  ✗ {nome} {detalhe}")


# ---------------------------------------------------------------- normalização
print("\nnormalize_reference_period")
check("MM/AAAA -> AAAA-MM", normalize_reference_period("07/2026") == "2026-07")
check("mês sem zero à esquerda", normalize_reference_period("7/2026") == "2026-07")
check("já normalizado é idempotente", normalize_reference_period("2026-07") == "2026-07")
check("None continua None", normalize_reference_period(None) is None)


# ---------------------------------------------------------------------- estado
print("\nRunState — ciclo de vida e retomada")
tmp = Path(tempfile.mkdtemp(prefix="cnpj-state-"))
try:
    st = RunState.load_or_create("2026-07", state_dir=tmp)
    check("cria arquivo do período", (tmp / "pipeline_state_2026-07.json").exists())
    check("nome do arquivo usa o período, não a data de hoje",
          st.path.name == "pipeline_state_2026-07.json")
    check("todas as etapas começam pending",
          all(s["status"] == "pending" for s in st.data["steps"]))
    check("tem run_id e created_at com offset",
          bool(st.run_id) and "+" in st.data["created_at"] or "-" in st.data["created_at"][10:])

    # executa duas etapas
    executadas = []
    run_step(st, STEP_CARGA, lambda: executadas.append(STEP_CARGA))
    check("etapa executada vira success", st.is_done(STEP_CARGA))

    # falha numa etapa
    def explode():
        raise RuntimeError("estouro simulado")

    try:
        run_step(st, STEP_VIEWS, explode)
    except RuntimeError:
        pass
    check("etapa que falha vira failed",
          st._step(STEP_VIEWS)["status"] == STATUS_FAILED)
    check("erro é registrado", "estouro simulado" in (st._step(STEP_VIEWS)["error"] or ""))
    check("pipeline inteiro marcado failed", st.data["status"] == "failed")

    # --- retomada: nova instância lê o mesmo período
    st2 = RunState.load_or_create("2026-07", state_dir=tmp)
    check("retomada preserva o run_id", st2.run_id == st.run_id)
    check("retomada reconhece etapa concluída", st2.is_done(STEP_CARGA))

    executadas.clear()
    run_step(st2, STEP_CARGA, lambda: executadas.append("NAO DEVERIA RODAR"))
    check("etapa concluída é PULADA na retomada", executadas == [],
          f"(executou: {executadas})")

    run_step(st2, STEP_VIEWS, lambda: executadas.append(STEP_VIEWS))
    check("etapa que falhou é RETENTADA", executadas == [STEP_VIEWS])
    check("tentativas são contadas", st2._step(STEP_VIEWS)["attempts"] == 2)

    # --- a janela é do dado, não do relógio
    st3 = RunState.load_or_create("2026-07", state_dir=tmp)
    check("execução em outro dia continua no mesmo estado do período",
          st3.run_id == st.run_id and st3.is_done(STEP_CARGA))

    # --- force
    run_id_antigo = st3.run_id
    st4 = RunState.load_or_create("2026-07", state_dir=tmp, force=True)
    check("--force gera novo run_id", st4.run_id != run_id_antigo)
    check("--force zera as etapas",
          all(s["status"] == "pending" for s in st4.data["steps"]))
    backups = list(tmp.glob("pipeline_state_2026-07.json.bak-*"))
    check("--force preserva backup do estado anterior", len(backups) == 1,
          f"(encontrados: {[b.name for b in backups]})")

    # --- limite de tentativas
    st5 = RunState.load_or_create("2026-08", state_dir=tmp, max_attempts=2)
    for _ in range(2):
        try:
            run_step(st5, STEP_VIEWS, explode)
        except RuntimeError:
            pass
    try:
        run_step(st5, STEP_VIEWS, explode)
        check("aborta após max-attempts", False, "(não levantou)")
    except RuntimeError as exc:
        check("aborta após max-attempts com instrução acionável",
              "--force" in str(exc))

    # --- estado corrompido não derruba o pipeline
    (tmp / "pipeline_state_2026-09.json").write_text("{lixo nao json", encoding="utf-8")
    st6 = RunState.load_or_create("2026-09", state_dir=tmp)
    check("estado corrompido é recriado em vez de quebrar",
          len(st6.data["steps"]) == len(PIPELINE_STEPS))

    # --- sem estado, run_step apenas executa
    chamou = []
    run_step(None, STEP_CARGA, lambda: chamou.append(1))
    check("sem estado, run_step executa normalmente", chamou == [1])

    # --- latest_period
    check("latest_period encontra o mais recente", RunState.latest_period(tmp) is not None)

    # --- progresso dentro de uma etapa (carga/download)
    import time as _t
    st7 = RunState.load_or_create("2026-11", state_dir=tmp)
    st7.start(STEP_CARGA)
    st7.progress(STEP_CARGA, tabela_atual="empresa", records_inserted=1000, percentual=1.0)
    meta = st7._step(STEP_CARGA)["metadata"]
    check("progress publica metadados da etapa",
          meta.get("tabela_atual") == "empresa" and meta.get("records_inserted") == 1000)

    # throttle: chamadas seguidas acumulam em memória sem gravar toda vez
    mtime_antes = st7.path.stat().st_mtime
    for i in range(200):
        st7.progress(STEP_CARGA, records_inserted=2000 + i)
    check("progress acumula em memória (throttle de escrita)",
          st7._step(STEP_CARGA)["metadata"]["records_inserted"] == 2199)
    check("throttle evitou 200 gravações", st7.path.stat().st_mtime == mtime_antes,
          "(gravou a cada chamada)")

    # passado o intervalo, a próxima chamada grava
    st7._ultimo_progresso -= 10
    st7.progress(STEP_CARGA, records_inserted=9999)
    import json as _j
    check("depois do intervalo, o progresso é persistido",
          _j.loads(st7.path.read_text())["steps"][3]["metadata"]["records_inserted"] == 9999)

    # --- ambiente e banco no estado
    st8 = RunState.load_or_create("2026-12", state_dir=tmp)
    st8.set_environment({"hostname": "maq", "runtime": "docker"}, {"database": "dados_cnpj"})
    salvo = _j.loads(st8.path.read_text())
    check("estado guarda o ambiente", salvo["environment"]["runtime"] == "docker")
    check("estado guarda o banco", salvo["database"]["database"] == "dados_cnpj")

    # db_info_fn é chamado ao concluir a etapa (banco "crescendo")
    chamadas = []
    st9 = RunState.load_or_create("2027-01", state_dir=tmp,
                                  db_info_fn=lambda: (chamadas.append(1), {"tamanho": f"{len(chamadas)} GB"})[1])
    run_step(st9, STEP_CARGA, lambda: None)
    check("db_info_fn atualiza o banco ao fim da etapa",
          st9.data.get("database", {}).get("tamanho") == "1 GB")

    # um coletor que explode não pode derrubar a etapa
    def _explode():
        raise RuntimeError("banco fora do ar")
    st10 = RunState.load_or_create("2027-02", state_dir=tmp, db_info_fn=_explode)
    run_step(st10, STEP_CARGA, lambda: None)
    check("coletor de banco com erro não quebra a etapa", st10.is_done(STEP_CARGA))

finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- ambiente
print("\nAmbiente de execução")
amb = collect_environment()
check("detecta hostname", bool(amb.get("hostname")))
check("informa o runtime", amb.get("runtime") in ("docker", "python"))
check("traz versão do Python", amb.get("python", "").count(".") >= 1)
check("traz sistema e arquitetura", bool(amb.get("so")) and bool(amb.get("arquitetura")))
check("traz PID e CPUs", isinstance(amb.get("pid"), int) and isinstance(amb.get("cpus"), int))
check("IP é um IPv4 ou None",
      amb.get("ip") is None or amb["ip"].count(".") == 3)

banco_off = collect_database_info({"host": "127.0.0.1", "port": 1, "user": "x",
                                   "password": "y", "database": "z"})
check("banco inacessível não levanta exceção", banco_off.get("acessivel") is False)
check("dados de destino são preservados mesmo offline",
      banco_off.get("database") == "z" and banco_off.get("porta") == 1)
check("senha NUNCA é coletada", "password" not in banco_off and "senha" not in banco_off)


# -------------------------------------------------------------------- webhooks
print("\nWebhookNotifier")
recebidos = []


class _Coletor(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        recebidos.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        return


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Coletor)
threading.Thread(target=srv.serve_forever, daemon=True).start()
porta = srv.server_address[1]

tmp2 = Path(tempfile.mkdtemp(prefix="cnpj-wh-"))
try:
    notifier = WebhookNotifier(url=f"http://127.0.0.1:{porta}/hook")
    st = RunState.load_or_create("2026-07", state_dir=tmp2, notifier=notifier)
    st.pipeline_started()
    run_step(st, STEP_CARGA, lambda: None)
    st.pipeline_finished()

    eventos = [e["event"] for e in recebidos]
    check("emite pipeline_started", "pipeline_started" in eventos)
    check("emite step_started", "step_started" in eventos)
    check("emite step_completed", "step_completed" in eventos)
    check("emite pipeline_completed", "pipeline_completed" in eventos)

    step_ev = next(e for e in recebidos if e["event"] == "step_completed")
    check("payload tem as chaves do contrato",
          set(step_ev) == {"event", "run_id", "reference_period", "step", "timestamp"},
          f"(veio: {sorted(step_ev)})")
    check("step do payload tem as chaves do contrato",
          set(step_ev["step"]) == {"name", "status", "started_at", "finished_at", "error"},
          f"(veio: {sorted(step_ev['step'])})")
    check("status do step é success", step_ev["step"]["status"] == STATUS_SUCCESS)

    # --- resiliência: destino inexistente não pode derrubar o pipeline
    ruim = WebhookNotifier(url="http://127.0.0.1:9/inexistente", timeout=1)
    st_r = RunState.load_or_create("2026-10", state_dir=tmp2, notifier=ruim)
    houve_erro = False
    try:
        st_r.pipeline_started()
        run_step(st_r, STEP_CARGA, lambda: None)
        st_r.pipeline_finished()
    except Exception:
        houve_erro = True
    check("webhook inacessível NÃO interrompe o pipeline", not houve_erro)
    check("etapa concluiu mesmo com webhook falhando", st_r.is_done(STEP_CARGA))

    # --- prioridade da flag sobre a env
    import os
    os.environ["PIPELINE_WEBHOOK_URL"] = "http://env.example"
    n1 = WebhookNotifier.from_config("http://flag.example")
    check("flag tem prioridade sobre env", n1.url == "http://flag.example")
    n2 = WebhookNotifier.from_config(None)
    check("env é usada quando não há flag", n2.url == "http://env.example")
    del os.environ["PIPELINE_WEBHOOK_URL"]
    check("sem url, não há notifier", WebhookNotifier.from_config(None) is None)

    # ---------------------------------------------------------------- dashboard
    print("\nDashboard — autenticação")
    import base64

    def get(porta_, rota, user=None, senha=None):
        req = urllib.request.Request(f"http://127.0.0.1:{porta_}{rota}")
        if user is not None:
            cred = base64.b64encode(f"{user}:{senha}".encode()).decode()
            req.add_header("Authorization", "Basic " + cred)
        return urllib.request.urlopen(req, timeout=5)

    dash = start_dashboard(state_path=st.path, port=0, host="127.0.0.1",
                           password="senha-secreta", user="pipeline")
    check("sobe sem erro", dash is not None)
    if dash:
        p = dash.server_address[1]

        # sem credencial -> 401 com desafio
        try:
            get(p, "/")
            check("sem credencial retorna 401", False, "(deixou passar!)")
        except urllib.error.HTTPError as e:
            check("sem credencial retorna 401", e.code == 401)
            check("envia WWW-Authenticate (prompt do navegador)",
                  "Basic" in (e.headers.get("WWW-Authenticate") or ""))

        # senha errada -> 401
        try:
            get(p, "/", "pipeline", "errada")
            check("senha errada retorna 401", False, "(deixou passar!)")
        except urllib.error.HTTPError as e:
            check("senha errada retorna 401", e.code == 401)

        # usuário errado -> 401
        try:
            get(p, "/", "outro", "senha-secreta")
            check("usuário errado retorna 401", False, "(deixou passar!)")
        except urllib.error.HTTPError as e:
            check("usuário errado retorna 401", e.code == 401)

        # o JSON também é protegido
        try:
            get(p, "/state.json")
            check("state.json exige credencial", False, "(vazou o estado!)")
        except urllib.error.HTTPError as e:
            check("state.json exige credencial", e.code == 401)

        # credencial correta
        html = get(p, "/", "pipeline", "senha-secreta").read().decode()
        check("com credencial serve o HTML", "<html" in html.lower())
        check("usa Alpine.js via CDN", "alpinejs" in html and "cdn.jsdelivr.net" in html)
        check("CSS é inline (sem folha de estilo externa)",
              "stylesheet" not in html and "<style>" in html)
        check("tem seletor de intervalo de atualização", 'x-model.number="intervalo"' in html)
        check("intervalo padrão é 6s", ">6s<" in html)

        # --- não indexável (meta + header, este último cobre o state.json)
        check("meta robots com noindex",
              'name="robots"' in html and "noindex" in html)
        r_html = get(p, "/", "pipeline", "senha-secreta")
        check("header X-Robots-Tag no HTML",
              "noindex" in (r_html.headers.get("X-Robots-Tag") or ""))
        r_json = get(p, "/state.json", "pipeline", "senha-secreta")
        check("header X-Robots-Tag no state.json",
              "noindex" in (r_json.headers.get("X-Robots-Tag") or ""))
        check("X-Content-Type-Options: nosniff",
              r_html.headers.get("X-Content-Type-Options") == "nosniff")
        check("Referrer-Policy: no-referrer",
              r_html.headers.get("Referrer-Policy") == "no-referrer")

        # --- título e semântica
        check("tem <title> descritivo", "<title>CNPJ Pipeline" in html)
        check("título da aba vira dinâmico no JS", "document.title" in html)
        check("landmarks: main/header/footer", all(
              t in html for t in ("<main", "<header", "<footer")))
        check("seções com <section> rotuladas", "aria-labelledby" in html)
        check("métricas em <dl>/<dt>/<dd>", "<dl" in html and "<dt>" in html and "<dd" in html)
        check("usa <progress> nativo", "<progress" in html)
        check("select tem <label>", 'for="intervalo"' in html)
        check("regiões dinâmicas com aria-live", "aria-live" in html)
        check("respeita prefers-reduced-motion", "prefers-reduced-motion" in html)
        check("tem <noscript> com alternativa", "<noscript>" in html)
        check("favicon embutido (sem 404)", 'rel="icon"' in html)
        check("declara color-scheme", 'name="color-scheme"' in html)
        check("idioma pt-BR", 'lang="pt-BR"' in html)
        estado = json.loads(get(p, "/state.json", "pipeline", "senha-secreta").read())
        check("serve o JSON de estado", estado["run_id"] == st.run_id)
        try:
            get(p, "/qualquer", "pipeline", "senha-secreta")
            check("404 em rota desconhecida", False)
        except urllib.error.HTTPError as e:
            check("404 em rota desconhecida", e.code == 404)
        dash.shutdown(); dash.server_close()

    # senha gerada automaticamente quando não informada
    from rfb_cnpj_etl.utils.dashboard import gerar_senha
    d2 = start_dashboard(state_path=st.path, port=0, host="127.0.0.1")
    if d2:
        p2 = d2.server_address[1]
        try:
            get(p2, "/")
            check("sem senha informada, ainda assim exige auth", False, "(subiu aberto!)")
        except urllib.error.HTTPError as e:
            check("sem senha informada, ainda assim exige auth", e.code == 401)
        d2.shutdown(); d2.server_close()
    check("gerar_senha produz senha não trivial", len(gerar_senha()) >= 10)
    check("gerar_senha não repete", gerar_senha() != gerar_senha())

    # --no-auth serve aberto conscientemente
    d3 = start_dashboard(state_path=st.path, port=0, host="127.0.0.1", auth=False)
    if d3:
        p3 = d3.server_address[1]
        check("--no-auth serve sem credencial", get(p3, "/").status == 200)
        d3.shutdown(); d3.server_close()

    # porta ocupada não derruba nada
    ocupada = start_dashboard(state_path=st.path, port=porta, host="127.0.0.1")
    check("porta ocupada retorna None em vez de explodir", ocupada is None)

finally:
    srv.shutdown()
    shutil.rmtree(tmp2, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
