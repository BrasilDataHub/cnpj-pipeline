#!/usr/bin/env python3
"""
`pipeline_stats` table tests against a real PostgreSQL.

Run (starts and tears down a disposable Docker Postgres):
    python3 tests/test_pipeline_stats.py

Or pointing to an existing database:
    PGTEST_DSN='postgresql://postgres:senha@localhost:5432/teste' \
        python3 tests/test_pipeline_stats.py

The most important test here is the last one: `drop_tables()` sweeps
`pg_tables` of the public schema, so without the preservation list it would
erase the run history on every reload.
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
PASSWORD = "teste-local"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def start_postgres():
    """Starts an ephemeral Postgres and returns its DSN."""
    subprocess.run(["docker", "rm", "-f", CONTAINER],
                   capture_output=True, check=False)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER,
         "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_DB=teste",
         "-P", "postgres:17-alpine"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ! não foi possível subir o Postgres: {r.stderr.strip()}")
        return None
    port = subprocess.run(
        ["docker", "port", CONTAINER, "5432/tcp"],
        capture_output=True, text=True,
    ).stdout.strip().rsplit(":", 1)[-1]

    dsn = f"postgresql://postgres:{PASSWORD}@127.0.0.1:{port}/teste"
    for _ in range(60):
        try:
            psycopg2.connect(dsn).close()
            return dsn
        except psycopg2.Error:
            time.sleep(1)
    print("  ! Postgres não ficou pronto a tempo")
    return None


dsn = os.getenv("PGTEST_DSN")
ephemeral = dsn is None
if ephemeral:
    print("subindo Postgres efêmero em Docker…")
    dsn = start_postgres()
if not dsn:
    print("SKIP: sem banco disponível para testar")
    sys.exit(0)

try:
    conn = psycopg2.connect(dsn)

    print("\npipeline_stats — schema and lifecycle")
    check("ensure_table creates the table", pipeline_stats.ensure_table(conn))
    check("ensure_table is idempotent", pipeline_stats.ensure_table(conn))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_name = 'pipeline_stats' ORDER BY ordinal_position;
        """)
        columns = dict(cur.fetchall())

    expected = {
        "run_id", "reference_period", "status", "started_at", "finished_at",
        "duration_seconds", "records_inserted_total", "tables_populated",
        "files_downloaded_count", "files_downloaded_detail",
        "views_refreshed_at", "error",
    }
    check("all spec columns exist", expected <= set(columns),
          f"(missing: {expected - set(columns)})")
    check("started_at is timestamptz (date+time+offset in one field)",
          columns.get("started_at") == "timestamp with time zone",
          f"(got: {columns.get('started_at')})")
    check("finished_at is timestamptz",
          columns.get("finished_at") == "timestamp with time zone")
    check("tables_populated is jsonb", columns.get("tables_populated") == "jsonb")
    check("files_downloaded_detail is jsonb",
          columns.get("files_downloaded_detail") == "jsonb")
    check("views_refreshed_at is timestamptz",
          columns.get("views_refreshed_at") == "timestamp with time zone",
          f"(got: {columns.get('views_refreshed_at')})")

    # --- migration: a pre-existing table without the column gains it
    with conn.cursor() as cur:
        conn.autocommit = True
        cur.execute("ALTER TABLE pipeline_stats DROP COLUMN views_refreshed_at;")
    pipeline_stats.ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM information_schema.columns
             WHERE table_name = 'pipeline_stats'
               AND column_name = 'views_refreshed_at';
        """)
        check("ensure_table migrates views_refreshed_at into old tables",
              cur.fetchone()[0] == 1)

    # --- full lifecycle
    run_id = str(uuid.uuid4())
    started = now_iso()
    check("start_run writes started_at",
          pipeline_stats.start_run(conn, run_id, "2026-07", started))

    with conn.cursor() as cur:
        cur.execute("SELECT status, reference_period FROM pipeline_stats WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
    check("row is born in_progress", row[0] == "in_progress")
    check("reference period is written", row[1] == "2026-07")

    # repeated start_run (resume) neither duplicates nor overwrites the start
    pipeline_stats.start_run(conn, run_id, "2026-07", now_iso())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats WHERE run_id=%s", (run_id,))
        check("resume does not duplicate the run row", cur.fetchone()[0] == 1)

    downloads = [
        {"filename": "Empresas0.zip", "size_bytes": 48213120,
         "source_url": "https://exemplo/Empresas0.zip", "downloaded_at": now_iso()},
        {"filename": "Socios0.zip", "size_bytes": 12000,
         "source_url": "https://exemplo/Socios0.zip", "downloaded_at": now_iso()},
    ]
    time.sleep(1)   # guarantees duration > 0
    views_ts = now_iso()
    check("finish_run closes the run", pipeline_stats.finish_run(
        conn, run_id=run_id, finished_at=now_iso(), status="completed",
        records_inserted_total=218_380_000,
        tables_populated=[{"table": "estabelecimento", "rows": 72318968}],
        files_downloaded=downloads,
        views_refreshed_at=views_ts,
    ))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT status, records_inserted_total, files_downloaded_count,
                   duration_seconds, tables_populated, files_downloaded_detail
              FROM pipeline_stats WHERE run_id=%s
        """, (run_id,))
        (status, records, file_count, duration, tables, detail) = cur.fetchone()

    check("final status written", status == "completed")
    check("records_inserted_total written", records == 218_380_000)
    check("files_downloaded_count derived from the detail", file_count == 2)
    check("duration_seconds computed", duration is not None and float(duration) >= 1,
          f"(got: {duration})")
    check("tables_populated is queryable JSON",
          tables[0]["table"] == "estabelecimento")
    check("files_downloaded_detail has the 4 spec fields",
          set(detail[0]) == {"filename", "size_bytes", "source_url", "downloaded_at"},
          f"(got: {sorted(detail[0])})")

    with conn.cursor() as cur:
        cur.execute("SELECT views_refreshed_at FROM pipeline_stats WHERE run_id=%s",
                    (run_id,))
        check("views_refreshed_at written by finish_run",
              cur.fetchone()[0] is not None)

    # --- a partial resume must not zero out what was already measured
    pipeline_stats.finish_run(conn, run_id=run_id, finished_at=now_iso(),
                              status="completed")
    with conn.cursor() as cur:
        cur.execute("SELECT records_inserted_total, files_downloaded_count, "
                    "views_refreshed_at FROM pipeline_stats WHERE run_id=%s",
                    (run_id,))
        r2 = cur.fetchone()
    check("resume without metrics preserves the previous totals",
          r2[0] == 218_380_000 and r2[1] == 2, f"(got: {r2})")
    check("resume without views preserves views_refreshed_at",
          r2[2] is not None)

    # --- partial status (run ended clean but with required steps pending)
    partial_id = str(uuid.uuid4())
    pipeline_stats.start_run(conn, partial_id, "2026-07", now_iso())
    pipeline_stats.finish_run(conn, run_id=partial_id, finished_at=now_iso(),
                              status="partial")
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM pipeline_stats WHERE run_id=%s",
                    (partial_id,))
        check("finish_run records status=partial", cur.fetchone()[0] == "partial")

    # --- record_views_refresh stamps the most recent run of the period
    print("\nrecord_views_refresh — manual refresh signal")
    other_id = str(uuid.uuid4())
    pipeline_stats.start_run(conn, other_id, "2026-06", now_iso())
    stamp = now_iso()
    check("record_views_refresh returns True",
          pipeline_stats.record_views_refresh(conn, stamp,
                                              reference_period="2026-06"))
    with conn.cursor() as cur:
        cur.execute("SELECT views_refreshed_at FROM pipeline_stats WHERE run_id=%s",
                    (other_id,))
        check("stamps the run of the given period", cur.fetchone()[0] is not None)
        cur.execute("SELECT views_refreshed_at FROM pipeline_stats WHERE run_id=%s",
                    (partial_id,))
        first_partial_stamp = cur.fetchone()[0]
        check("does not touch runs of other periods when period is given",
              first_partial_stamp is None)
    check("record_views_refresh without period targets the latest run",
          pipeline_stats.record_views_refresh(conn, now_iso()))
    with conn.cursor() as cur:
        cur.execute("""
            SELECT views_refreshed_at FROM pipeline_stats
             ORDER BY started_at DESC LIMIT 1;
        """)
        check("latest run got the stamp", cur.fetchone()[0] is not None)
    check("record_views_refresh on a period with no runs is a no-op",
          pipeline_stats.record_views_refresh(conn, now_iso(),
                                              reference_period="1999-01"))

    # --- the historical query the spec wants to enable
    with conn.cursor() as cur:
        cur.execute("""
            SELECT duration_seconds FROM pipeline_stats
             WHERE reference_period = '2026-07'
             ORDER BY started_at DESC LIMIT 1;
        """)
        check("'how long did the last run take' query works",
              cur.fetchone() is not None)

    # --- the test that matters most: surviving drop_tables()
    print("\ndrop_tables — history preservation")
    with conn.cursor() as cur:
        conn.autocommit = True
        cur.execute("CREATE TABLE IF NOT EXISTS empresa (cnpj_basico varchar(8));")
        cur.execute("CREATE TABLE IF NOT EXISTS socio (cnpj_basico varchar(8));")

    from rfb_cnpj_etl.db.postgres_builder import PostgresBuilder, PRESERVED_TABLES
    check("pipeline_stats is on the preservation list",
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
        remaining = {t for (t,) in cur.fetchall()}
    check("drop_tables removed the data tables",
          "empresa" not in remaining and "socio" not in remaining,
          f"(left: {remaining})")
    check("drop_tables PRESERVED pipeline_stats", "pipeline_stats" in remaining,
          f"(left: {remaining})")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats WHERE run_id=%s", (run_id,))
        check("run history survived the reload", cur.fetchone()[0] == 1)

    conn.close()

finally:
    if ephemeral:
        subprocess.run(["docker", "rm", "-f", CONTAINER],
                       capture_output=True, check=False)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
