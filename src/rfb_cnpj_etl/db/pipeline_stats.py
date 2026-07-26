# db/pipeline_stats.py

"""
Tabela de estatísticas por execução do pipeline.

Uma **linha por execução** (chaveada por `run_id`), não pares chave/valor
soltos: é o que permite responder "quanto tempo levou a última execução" ou
"quantos arquivos foram baixados na execução de tal data" com um SELECT
trivial, e manter histórico comparável entre meses.

Todos os instantes são `TIMESTAMPTZ` — data, hora e fuso num único campo,
nunca em colunas separadas.

A tabela é criada com `IF NOT EXISTS` e é **preservada por drop_tables()**
(ver postgres_builder). Sem isso, cada recarga apagaria todo o histórico, já
que o drop varre `pg_tables` do schema `public`.
"""

import json
from typing import Any, Dict, List, Optional

import psycopg2

from ..config import PIPELINE_STATS_TABLE
from ..utils.logger import print_log

# Tabelas de domínio/carga cuja população interessa reportar. Excluímos as
# tabelas auxiliares (IBGE) por serem estáticas entre execuções.
_TABELAS_DE_CARGA = (
    "empresa", "estabelecimento", "socio", "simples",
    "estabelecimento_cnae_sec", "cnae", "natureza_juridica",
    "qualificacao_socio", "motivo", "pais", "municipio",
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
    """Cria a tabela se ainda não existir. Retorna False se não foi possível.

    Estatística é instrumentação: um problema aqui é logado, mas nunca
    interrompe a carga.
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
    """Registra o início da execução (`started_at`).

    `ON CONFLICT DO NOTHING` porque uma retomada reutiliza o mesmo `run_id`:
    a linha já existe e o início original deve ser mantido.
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
    """Lista as tabelas populadas e quantas linhas cada uma tem.

    Usa a contagem viva do catálogo (`pg_stat_user_tables.n_live_tup`), não
    `COUNT(*)`: em `estabelecimento` (72M linhas) a contagem exata custaria
    minutos de I/O só para preencher um campo de metadado.
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
                (list(_TABELAS_DE_CARGA),),
            )
            return [{"tabela": nome, "linhas": int(linhas)} for nome, linhas in cur.fetchall()]
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
    """Fecha a linha da execução com os totais finais.

    `COALESCE` nos campos numéricos: uma retomada que só rodou as views não
    deve zerar o total de registros gravado pela execução que fez a carga.
    """
    if not ensure_table(conn):
        return False

    detalhe = json.dumps(files_downloaded, ensure_ascii=False) if files_downloaded is not None else None
    tabelas = json.dumps(tables_populated, ensure_ascii=False) if tables_populated is not None else None
    quantidade = len(files_downloaded) if files_downloaded is not None else None

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
                       tables_populated        = COALESCE(%(tabelas)s::jsonb, tables_populated),
                       files_downloaded_count  = COALESCE(%(quantidade)s, files_downloaded_count),
                       files_downloaded_detail = COALESCE(%(detalhe)s::jsonb, files_downloaded_detail),
                       error                   = %(error)s
                 WHERE run_id = %(run_id)s;
                """,
                {
                    "finished_at": finished_at,
                    "status": status,
                    "records": records_inserted_total,
                    "tabelas": tabelas,
                    "quantidade": quantidade,
                    "detalhe": detalhe,
                    "error": error,
                    "run_id": run_id,
                },
            )
        return True
    except psycopg2.Error as exc:
        print_log(f"FALHA AO FINALIZAR '{PIPELINE_STATS_TABLE}': {exc}", level="warning")
        return False
