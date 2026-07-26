#!/usr/bin/env python3
"""
Testes da tabela `pipeline_stats` contra um PostgreSQL real.

Rodar (sobe e derruba um Postgres descartável em Docker):
    python3 tests/test_pipeline_stats.py

Ou apontando para um banco já existente:
    PGTEST_DSN='postgresql://postgres:senha@localhost:5432/teste' \
        python3 tests/test_pipeline_stats.py

O teste mais importante aqui é o último: `drop_tables()` varre `pg_tables` do
schema public, então sem a lista de preservação ele apagaria o histórico de
execuções a cada recarga.
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg2  # noqa: E402

from rfb_cnpj_etl.db import pipeline_stats  # noqa: E402
from rfb_cnpj_etl.utils.run_state import now_iso  # noqa: E402

PASS = FAIL = 0
CONTAINER = "cnpj-stats-test"
SENHA = "teste-local"


def check(nome, condicao, detalhe=""):
    global PASS, FAIL
    if condicao:
        PASS += 1
        print(f"  ✓ {nome}")
    else:
        FAIL += 1
        print(f"  ✗ {nome} {detalhe}")


def subir_postgres():
    """Sobe um Postgres efêmero e devolve o DSN."""
    subprocess.run(["docker", "rm", "-f", CONTAINER],
                   capture_output=True, check=False)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER,
         "-e", f"POSTGRES_PASSWORD={SENHA}", "-e", "POSTGRES_DB=teste",
         "-P", "postgres:17-alpine"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ! não foi possível subir o Postgres: {r.stderr.strip()}")
        return None
    porta = subprocess.run(
        ["docker", "port", CONTAINER, "5432/tcp"],
        capture_output=True, text=True,
    ).stdout.strip().rsplit(":", 1)[-1]

    dsn = f"postgresql://postgres:{SENHA}@127.0.0.1:{porta}/teste"
    for _ in range(60):
        try:
            psycopg2.connect(dsn).close()
            return dsn
        except psycopg2.Error:
            time.sleep(1)
    print("  ! Postgres não ficou pronto a tempo")
    return None


dsn = os.getenv("PGTEST_DSN")
efemero = dsn is None
if efemero:
    print("subindo Postgres efêmero em Docker…")
    dsn = subir_postgres()
if not dsn:
    print("SKIP: sem banco disponível para testar")
    sys.exit(0)

try:
    conn = psycopg2.connect(dsn)

    print("\npipeline_stats — schema e ciclo de vida")
    check("ensure_table cria a tabela", pipeline_stats.ensure_table(conn))
    check("ensure_table é idempotente", pipeline_stats.ensure_table(conn))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_name = 'pipeline_stats' ORDER BY ordinal_position;
        """)
        colunas = dict(cur.fetchall())

    esperadas = {
        "run_id", "reference_period", "status", "started_at", "finished_at",
        "duration_seconds", "records_inserted_total", "tables_populated",
        "files_downloaded_count", "files_downloaded_detail", "error",
    }
    check("todas as colunas da spec existem", esperadas <= set(colunas),
          f"(faltando: {esperadas - set(colunas)})")
    check("started_at é timestamptz (data+hora+fuso num campo só)",
          colunas.get("started_at") == "timestamp with time zone",
          f"(veio: {colunas.get('started_at')})")
    check("finished_at é timestamptz",
          colunas.get("finished_at") == "timestamp with time zone")
    check("tables_populated é jsonb", colunas.get("tables_populated") == "jsonb")
    check("files_downloaded_detail é jsonb",
          colunas.get("files_downloaded_detail") == "jsonb")

    # --- ciclo completo
    run_id = str(uuid.uuid4())
    inicio = now_iso()
    check("start_run grava started_at",
          pipeline_stats.start_run(conn, run_id, "2026-07", inicio))

    with conn.cursor() as cur:
        cur.execute("SELECT status, reference_period FROM pipeline_stats WHERE run_id=%s", (run_id,))
        linha = cur.fetchone()
    check("linha nasce in_progress", linha[0] == "in_progress")
    check("período de referência é gravado", linha[1] == "2026-07")

    # start_run repetido (retomada) não duplica nem sobrescreve o início
    pipeline_stats.start_run(conn, run_id, "2026-07", now_iso())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats WHERE run_id=%s", (run_id,))
        check("retomada não duplica a linha da execução", cur.fetchone()[0] == 1)

    downloads = [
        {"nome_arquivo": "Empresas0.zip", "tamanho_bytes": 48213120,
         "url_origem": "https://exemplo/Empresas0.zip", "baixado_em": now_iso()},
        {"nome_arquivo": "Socios0.zip", "tamanho_bytes": 12000,
         "url_origem": "https://exemplo/Socios0.zip", "baixado_em": now_iso()},
    ]
    time.sleep(1)   # garante duração > 0
    check("finish_run fecha a execução", pipeline_stats.finish_run(
        conn, run_id=run_id, finished_at=now_iso(), status="completed",
        records_inserted_total=218_380_000,
        tables_populated=[{"tabela": "estabelecimento", "linhas": 72318968}],
        files_downloaded=downloads,
    ))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT status, records_inserted_total, files_downloaded_count,
                   duration_seconds, tables_populated, files_downloaded_detail
              FROM pipeline_stats WHERE run_id=%s
        """, (run_id,))
        (status, registros, qtd_arq, duracao, tabelas, detalhe) = cur.fetchone()

    check("status final gravado", status == "completed")
    check("records_inserted_total gravado", registros == 218_380_000)
    check("files_downloaded_count derivado do detalhe", qtd_arq == 2)
    check("duration_seconds calculado", duracao is not None and float(duracao) >= 1,
          f"(veio: {duracao})")
    check("tables_populated é JSON consultável",
          tabelas[0]["tabela"] == "estabelecimento")
    check("files_downloaded_detail tem os 4 campos da spec",
          set(detalhe[0]) == {"nome_arquivo", "tamanho_bytes", "url_origem", "baixado_em"},
          f"(veio: {sorted(detalhe[0])})")

    # --- uma retomada parcial não pode zerar o que já foi medido
    pipeline_stats.finish_run(conn, run_id=run_id, finished_at=now_iso(),
                              status="completed")
    with conn.cursor() as cur:
        cur.execute("SELECT records_inserted_total, files_downloaded_count "
                    "FROM pipeline_stats WHERE run_id=%s", (run_id,))
        r2 = cur.fetchone()
    check("retomada sem métricas preserva os totais anteriores",
          r2[0] == 218_380_000 and r2[1] == 2, f"(veio: {r2})")

    # --- consulta histórica que a spec quer viabilizar
    with conn.cursor() as cur:
        cur.execute("""
            SELECT duration_seconds FROM pipeline_stats
             WHERE reference_period = '2026-07'
             ORDER BY started_at DESC LIMIT 1;
        """)
        check("consulta 'quanto durou a última execução' funciona",
              cur.fetchone() is not None)

    # --- o teste que mais importa: sobreviver ao drop_tables()
    print("\ndrop_tables — preservação do histórico")
    with conn.cursor() as cur:
        conn.autocommit = True
        cur.execute("CREATE TABLE IF NOT EXISTS empresa (cnpj_basico varchar(8));")
        cur.execute("CREATE TABLE IF NOT EXISTS socio (cnpj_basico varchar(8));")

    from rfb_cnpj_etl.db.postgres_builder import PostgresBuilder, PRESERVED_TABLES
    check("pipeline_stats está na lista de preservação",
          "pipeline_stats" in PRESERVED_TABLES)

    import urllib.parse as _u
    p = _u.urlparse(dsn)
    builder = PostgresBuilder(config={
        "host": p.hostname, "port": p.port, "user": p.username,
        "password": p.password, "database": p.path.lstrip("/"),
    })
    builder.drop_tables()

    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public';")
        restantes = {t for (t,) in cur.fetchall()}
    check("drop_tables removeu as tabelas de dados",
          "empresa" not in restantes and "socio" not in restantes,
          f"(restaram: {restantes})")
    check("drop_tables PRESERVOU pipeline_stats", "pipeline_stats" in restantes,
          f"(restaram: {restantes})")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats WHERE run_id=%s", (run_id,))
        check("histórico de execuções sobreviveu à recarga", cur.fetchone()[0] == 1)

    conn.close()

finally:
    if efemero:
        subprocess.run(["docker", "rm", "-f", CONTAINER],
                       capture_output=True, check=False)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
