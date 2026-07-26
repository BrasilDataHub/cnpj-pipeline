# db/dead_letter.py

"""
Dead-letter batch management.

A COPY batch that fails all automatic retries during the load is preserved as
`<table>-<timestamp>.csv` (the exact CSV payload COPY rejected, windows-1252,
`;`-separated) plus a `.meta` sidecar (table, columns, source file, row
count) in the dead-letter directory. The load itself does NOT fail because of
it — a broken batch shipped by the RFB must never sink an hours-long run.

This module closes the loop: it lists what is parked there and re-attempts
the COPY, so the operator can either simply retry (transient causes) or fix
the CSV by hand (bad source data) and then retry:

    python etl.py db dead-letter            # list parked batches
    python etl.py db dead-letter --retry    # re-attempt the COPY of each one

Batches that load successfully are moved to `<dir>/processed/` — the audit
trail is kept, but they will not be retried again.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2

from ..config import DEAD_LETTER_DIR
from ..utils.logger import print_log

PROCESSED_SUBDIR = "processed"


def _parse_meta(meta_path: Path) -> Dict[str, str]:
    """Parses the `.meta` sidecar (simple key=value lines)."""
    data: Dict[str, str] = {}
    try:
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def list_dead_letters(dead_letter_dir: Optional[str] = None) -> List[Dict]:
    """Returns the parked batches as [{"csv": Path, "meta": {...}}, ...]."""
    base = Path(dead_letter_dir or DEAD_LETTER_DIR)
    if not base.is_dir():
        return []
    entries = []
    for csv_path in sorted(base.glob("*.csv")):
        meta_path = csv_path.with_suffix(".meta")
        entries.append({
            "csv": csv_path,
            "meta": _parse_meta(meta_path) if meta_path.exists() else {},
        })
    return entries


def show_dead_letters(dead_letter_dir: Optional[str] = None) -> int:
    """Prints the parked batches. Returns how many there are."""
    base = Path(dead_letter_dir or DEAD_LETTER_DIR)
    entries = list_dead_letters(dead_letter_dir)
    if not entries:
        print_log(f"NENHUM LOTE EM DEAD-LETTER ({base})", level="success")
        return 0

    print_log(f"{len(entries)} LOTE(S) EM DEAD-LETTER EM {base}:", level="task")
    for entry in entries:
        meta = entry["meta"]
        print_log(
            f"  -> {entry['csv'].name} | tabela: {meta.get('table', '?')} "
            f"| linhas: {meta.get('rows', '?')} "
            f"| origem: {os.path.basename(meta.get('source_file', '?'))}",
            level="docs"
        )
    print_log(
        "Analise/corrija os CSVs se necessário e reprocesse com: "
        "python etl.py db dead-letter --retry",
        level="docs"
    )
    return len(entries)


def retry_dead_letters(postgres_config: dict,
                       dead_letter_dir: Optional[str] = None) -> Dict[str, int]:
    """Re-attempts the COPY of every parked batch.

    Each batch is one transaction: on success, the `.csv`/`.meta` pair moves
    to `processed/`; on failure, it stays in place (edit the CSV and run
    again). Returns {"retried": N, "succeeded": N, "failed": N}.
    """
    base = Path(dead_letter_dir or DEAD_LETTER_DIR)
    entries = list_dead_letters(dead_letter_dir)
    result = {"retried": len(entries), "succeeded": 0, "failed": 0}

    if not entries:
        print_log(f"NENHUM LOTE EM DEAD-LETTER PARA REPROCESSAR ({base})", level="success")
        return result

    processed_dir = base / PROCESSED_SUBDIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    print_log(f"REPROCESSANDO {len(entries)} LOTE(S) DE DEAD-LETTER...", level="task")

    conn = psycopg2.connect(**postgres_config)
    conn.set_client_encoding("WIN1252")
    conn.autocommit = False
    try:
        cur = conn.cursor()
        for entry in entries:
            csv_path: Path = entry["csv"]
            meta = entry["meta"]
            table = meta.get("table")
            columns = [c for c in meta.get("columns", "").split(",") if c]

            if not table or not columns:
                result["failed"] += 1
                print_log(
                    f"  -> {csv_path.name}: SEM .meta VÁLIDO (tabela/colunas desconhecidas), PULANDO",
                    level="error"
                )
                continue

            copy_sql = (f'COPY "{table}" ({",".join(columns)}) '
                        f'FROM STDIN WITH (FORMAT csv, DELIMITER \';\', NULL \'\')')
            try:
                with open(csv_path, "rb") as payload:
                    cur.copy_expert(copy_sql, payload)
                conn.commit()
                result["succeeded"] += 1

                # Keep the audit trail, but out of the retry path.
                os.replace(csv_path, processed_dir / csv_path.name)
                meta_path = csv_path.with_suffix(".meta")
                if meta_path.exists():
                    os.replace(meta_path, processed_dir / meta_path.name)

                print_log(f"  -> {csv_path.name}: CARREGADO EM '{table}' E MOVIDO PARA processed/",
                          level="success")
            except psycopg2.Error as exc:
                conn.rollback()
                result["failed"] += 1
                print_log(
                    f"  -> {csv_path.name}: AINDA FALHA ({exc.pgerror or exc}). "
                    f"Corrija o CSV e rode novamente.",
                    level="error"
                )
        cur.close()
    finally:
        conn.close()

    if result["failed"]:
        print_log(
            f"REPROCESSAMENTO CONCLUÍDO: {result['succeeded']} OK, "
            f"{result['failed']} AINDA COM FALHA (permanecem em {base})",
            level="warning"
        )
    else:
        print_log(
            f"REPROCESSAMENTO CONCLUÍDO: {result['succeeded']} LOTE(S) CARREGADO(S)",
            level="success"
        )
    return result
