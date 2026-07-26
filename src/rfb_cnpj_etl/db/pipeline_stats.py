# db/pipeline_stats.py

"""
Per-run pipeline statistics table.

One **row per run** (keyed by `run_id`), not loose key/value pairs: that is
what allows answering "how long did the last run take" or "how many files
were downloaded on the run of such date" with a trivial SELECT, and keeps a
comparable history across months.

Every instant is `TIMESTAMPTZ` — date, time and timezone in a single field,
never in separate columns.

The table is created with `IF NOT EXISTS` and is **preserved by
drop_tables()** (see postgres_builder). Without that, every reload would wipe
the whole history, since the drop sweeps `pg_tables` of the `public` schema.
"""

import json
from typing import Any, Dict, List, Optional

import psycopg2

from ..config import PIPELINE_STATS_TABLE
from ..utils.logger import print_log

# Domain/load tables whose population is worth reporting. The auxiliary IBGE
# tables are excluded for being static across runs.
LOAD_TABLES = (
    "empresa", "estabelecimento", "socio", "simples",
    "estabelecimento_cnae_sec", "cnae", "natureza_juridica",
    "qualificacao_socio", "motivo", "pais", "municipio_rfb",
)

DDL = f"""
CREATE TABLE IF NOT EXISTS {PIPELINE_STATS_TABLE} (
    run_id                  UUID        PRIMARY KEY,
    reference_period        VARCHAR(7),
    status                  VARCHAR(20)  NOT NULL DEFAULT 'in_progress',
    started_at              TIMESTAMPTZ  NOT NULL,
    finished_at             TIMESTAMPTZ,
    duration_seconds        NUMERIC,
    records_inserted_total  BIGINT,
    tables_populated        JSONB,
    files_downloaded_count  INTEGER,
    files_downloaded_detail JSONB,
    error                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_{PIPELINE_STATS_TABLE}_periodo
    ON {PIPELINE_STATS_TABLE} (reference_period, started_at DESC);
"""


def ensure_table(conn) -> bool:
    """Creates the table if it does not exist yet. Returns False on failure.

    Statistics are instrumentation: a problem here is logged but never
    interrupts the load.
    """
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(DDL)
        return True
    except psycopg2.Error as exc:
        print_log(f"NÃO FOI POSSÍVEL CRIAR '{PIPELINE_STATS_TABLE}': {exc}", level="warning")
        return False


def start_run(conn, run_id: str, reference_period: Optional[str], started_at: str) -> bool:
    """Registers the run start (`started_at`).

    `ON CONFLICT DO NOTHING` because a resume reuses the same `run_id`: the
    row already exists and the original start must be kept.
    """
    if not ensure_table(conn):
        return False
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {PIPELINE_STATS_TABLE}
                       (run_id, reference_period, status, started_at)
                VALUES (%s, %s, 'in_progress', %s)
                ON CONFLICT (run_id) DO NOTHING;
                """,
                (run_id, reference_period, started_at),
            )
        return True
    except psycopg2.Error as exc:
        print_log(f"FALHA AO REGISTRAR INÍCIO EM '{PIPELINE_STATS_TABLE}': {exc}", level="warning")
        return False


def collect_tables_populated(conn) -> Optional[List[Dict[str, Any]]]:
    """Lists the populated tables and how many rows each one has.

    Uses the catalog's live count (`pg_stat_user_tables.n_live_tup`), not
    `COUNT(*)`: on `estabelecimento` (72M rows) an exact count would cost
    minutes of I/O just to fill a metadata field.
    """
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT relname, n_live_tup
                  FROM pg_stat_user_tables
                 WHERE schemaname = 'public'
                   AND relname = ANY(%s)
                   AND n_live_tup > 0
                 ORDER BY n_live_tup DESC;
                """,
                (list(LOAD_TABLES),),
            )
            return [{"table": name, "rows": int(rows)} for name, rows in cur.fetchall()]
    except psycopg2.Error as exc:
        print_log(f"FALHA AO COLETAR TABELAS POPULADAS: {exc}", level="warning")
        return None


def finish_run(
        conn,
        run_id: str,
        finished_at: str,
        status: str = "completed",
        records_inserted_total: Optional[int] = None,
        tables_populated: Optional[List[Dict[str, Any]]] = None,
        files_downloaded: Optional[List[Dict[str, Any]]] = None,
        error: Optional[str] = None,
) -> bool:
    """Closes the run row with the final totals.

    `COALESCE` on the numeric fields: a resume that only ran the views must
    not zero out the record total written by the run that did the load.
    """
    if not ensure_table(conn):
        return False

    detail = json.dumps(files_downloaded, ensure_ascii=False) if files_downloaded is not None else None
    tables = json.dumps(tables_populated, ensure_ascii=False) if tables_populated is not None else None
    count = len(files_downloaded) if files_downloaded is not None else None

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {PIPELINE_STATS_TABLE}
                   SET finished_at             = %(finished_at)s,
                       status                  = %(status)s,
                       duration_seconds        = EXTRACT(EPOCH FROM (%(finished_at)s::timestamptz - started_at)),
                       records_inserted_total  = COALESCE(%(records)s, records_inserted_total),
                       tables_populated        = COALESCE(%(tables)s::jsonb, tables_populated),
                       files_downloaded_count  = COALESCE(%(count)s, files_downloaded_count),
                       files_downloaded_detail = COALESCE(%(detail)s::jsonb, files_downloaded_detail),
                       error                   = %(error)s
                 WHERE run_id = %(run_id)s;
                """,
                {
                    "finished_at": finished_at,
                    "status": status,
                    "records": records_inserted_total,
                    "tables": tables,
                    "count": count,
                    "detail": detail,
                    "error": error,
                    "run_id": run_id,
                },
            )
        return True
    except psycopg2.Error as exc:
        print_log(f"FALHA AO FINALIZAR '{PIPELINE_STATS_TABLE}': {exc}", level="warning")
        return False
