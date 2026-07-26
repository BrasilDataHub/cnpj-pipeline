# db/postgres_loader.py

import gc
from datetime import datetime
from pathlib import Path

import psycopg2
from contextlib import contextmanager
from queue import Queue
from threading import Thread, Lock
from typing import Any, Dict, Generator, Optional
from ..config import QUEUE_SIZE, WORKER_THREADS, DEBUG_LOG, DEAD_LETTER_DIR
from ..utils.logger import print_log
from ..utils.progress import pbar, update_progress
from ..utils.db_batch_producer import produce_batches
from ..utils.db_transformers import convert_rows_to_csv_buffer

# COPY attempts per batch. The first failure is often transient (connection
# dropped, brief server hiccup); retrying with a fresh connection recovers it.
# A batch that still fails is written to the dead-letter directory and counted
# — the load step then FAILS at the end instead of losing data silently.
MAX_COPY_ATTEMPTS = 3


@contextmanager
def get_connection(config: dict) -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager for a PostgreSQL connection.
    Guarantees the connection is closed even on error.
    """
    conn = None
    try:
        conn = _open_connection(config)
        yield conn
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _open_connection(config: dict) -> psycopg2.extensions.connection:
    """Opens a loader connection with the session tuned for bulk COPY.

    `synchronous_commit = off` skips one WAL fsync per commit. The trade-off
    (losing the last commits on a crash) is irrelevant here: during the load
    the tables are UNLOGGED and the whole database is rebuilt from scratch
    anyway — the website only switches to it after the pipeline finishes.
    """
    conn = psycopg2.connect(**config)
    conn.set_client_encoding("WIN1252")
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SET synchronous_commit = off;")
    conn.commit()
    return conn


def _write_dead_letter(item: Dict[str, Any]) -> Optional[str]:
    """Persists a permanently failed batch for later inspection/reload.

    The file is the exact CSV payload that COPY rejected (windows-1252,
    `;`-separated), plus a `.meta` sidecar with table/columns/source file.
    Returns the file path, or None when even the dump fails.
    """
    try:
        dead_letter_dir = Path(DEAD_LETTER_DIR)
        dead_letter_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        base_name = f"{item['table']}-{stamp}"
        csv_path = dead_letter_dir / f"{base_name}.csv"

        buffer = convert_rows_to_csv_buffer(item["rows"])
        csv_path.write_bytes(buffer.getvalue())
        buffer.close()

        meta_path = dead_letter_dir / f"{base_name}.meta"
        meta_path.write_text(
            f"table={item['table']}\n"
            f"columns={','.join(item['columns'])}\n"
            f"source_file={item['filename']}\n"
            f"rows={len(item['rows'])}\n",
            encoding="utf-8"
        )
        return str(csv_path)
    except Exception as exc:
        print_log(f"FALHA AO GRAVAR DEAD-LETTER: {exc}", level="error")
        return None


def consume_batches(insertion_queue, postgres_config: dict, thread_id: int,
                    progress_lock, shared_progress, low_memory: bool, total_records: int,
                    on_progress=None):
    """
    Consumes batches from the insertion queue and COPYs them into PostgreSQL.

    Failure handling per batch: up to MAX_COPY_ATTEMPTS attempts (reconnecting
    when the connection is gone). A batch that exhausts its attempts goes to
    the dead-letter directory and is counted in `failed_batches`/`failed_rows`
    — it is never counted as inserted.
    """
    conn = None
    try:
        conn = _open_connection(postgres_config)
        cur = conn.cursor()

        while True:
            item = insertion_queue.get()
            if item is None:
                insertion_queue.task_done()
                break

            rows = item["rows"]

            if not rows:
                insertion_queue.task_done()
                continue

            table = item["table"]
            columns = item["columns"]
            copy_sql = (f'COPY "{table}" ({",".join(columns)}) '
                        f'FROM STDIN WITH (FORMAT csv, DELIMITER \';\', NULL \'\')')

            inserted = False
            last_error = None
            for attempt in range(1, MAX_COPY_ATTEMPTS + 1):
                buffer = None
                try:
                    buffer = convert_rows_to_csv_buffer(rows)
                    cur.copy_expert(copy_sql, buffer)
                    conn.commit()
                    inserted = True
                    break
                except psycopg2.Error as db_error:
                    last_error = f"{db_error.pgerror or db_error} (código: {db_error.pgcode})"
                    try:
                        conn.rollback()
                    except psycopg2.Error:
                        pass
                    # Dead connection: reopen before the next attempt.
                    if conn.closed:
                        try:
                            conn = _open_connection(postgres_config)
                            cur = conn.cursor()
                        except psycopg2.Error as reconnect_error:
                            last_error = f"reconexão falhou: {reconnect_error}"
                    if attempt < MAX_COPY_ATTEMPTS:
                        print_log(
                            f"ERRO DE DB INSERINDO EM '{table}' (tentativa {attempt}/{MAX_COPY_ATTEMPTS}): "
                            f"{last_error} | ARQUIVO: {item['filename']} — RETENTANDO...",
                            level="warning"
                        )
                except Exception as exc:
                    last_error = str(exc)
                    if attempt < MAX_COPY_ATTEMPTS:
                        print_log(
                            f"ERRO INSERINDO EM '{table}' (tentativa {attempt}/{MAX_COPY_ATTEMPTS}): "
                            f"{last_error} | ARQUIVO: {item['filename']} — RETENTANDO...",
                            level="warning"
                        )
                finally:
                    if buffer:
                        buffer.close()

            if inserted:
                # `estabelecimento_cnae_sec` rows come from the same source
                # lines as `estabelecimento`; counting both would double-count
                # against the estimated total.
                if table != 'estabelecimento_cnae_sec':
                    update_progress(
                        rows_inserted=len(rows),
                        filename=item['filename'],
                        insertion_queue=insertion_queue,
                        queue_size_max=QUEUE_SIZE,
                        shared=shared_progress,
                        lock=progress_lock,
                        total=total_records,
                        debug=DEBUG_LOG
                    )

                # After update_progress: that is what accumulates
                # inserted_total, so calling earlier would always publish the
                # previous batch's total.
                if on_progress is not None:
                    try:
                        on_progress(table, item["filename"],
                                    shared_progress.get("inserted_total", 0), total_records)
                    except Exception:
                        pass   # instrumentation must never interrupt the load
            else:
                dead_letter_path = _write_dead_letter(item)
                with progress_lock:
                    shared_progress["failed_batches"] = shared_progress.get("failed_batches", 0) + 1
                    shared_progress["failed_rows"] = shared_progress.get("failed_rows", 0) + len(rows)
                    if dead_letter_path:
                        shared_progress.setdefault("dead_letter_files", []).append(dead_letter_path)
                print_log(
                    f"LOTE PERDIDO EM '{table}' APÓS {MAX_COPY_ATTEMPTS} TENTATIVAS: {last_error} "
                    f"| ARQUIVO: {item['filename']} | {len(rows)} LINHAS GRAVADAS EM: "
                    f"{dead_letter_path or 'FALHA AO GRAVAR DEAD-LETTER'}",
                    level="error"
                )

            insertion_queue.task_done()
            if low_memory:
                gc.collect()

        cur.close()

    except Exception as fatal:
        print_log(f"[THREAD-{thread_id}] ERRO FATAL: {fatal}", level="error")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_postgres_loader(files_dir: str, postgres_config: dict, total_records: int, parallel: Optional[bool] = True,
                        low_memory: Optional[bool] = False, on_progress=None) -> Dict[str, Any]:
    """
    Runs the data load into PostgreSQL.

    :return: load metrics dict:
             - ``records_inserted``: rows effectively inserted (accumulated by
               the workers; excludes failed batches)
             - ``failed_batches`` / ``failed_rows``: batches/rows lost after
               retries — the caller must fail the load step when nonzero
             - ``dead_letter_files``: files with the rejected batches
    """
    print_log("REALIZANDO CARGA NO BANCO DE DADOS POSTGRES...", level="task")
    insertion_queue = Queue(maxsize=QUEUE_SIZE)

    progress_lock = Lock()
    shared_progress: Dict[str, Any] = {
        "inserted_total": 0,
        "queue_size": 0,
        "failed_batches": 0,
        "failed_rows": 0,
        "dead_letter_files": [],
    }

    if not DEBUG_LOG:
        progress = pbar(total=total_records)
        shared_progress["bar"] = progress

    workers = []
    num_threads = WORKER_THREADS if parallel else 1

    for i in range(num_threads):
        t = Thread(
            target=consume_batches,
            args=(insertion_queue, postgres_config, i + 1, progress_lock, shared_progress,
                  low_memory, total_records, on_progress)
        )
        t.start()
        workers.append(t)

    try:
        produce_batches(
            files_dir=files_dir,
            insertion_queue=insertion_queue,
            num_workers=num_threads,
            parallel=parallel,
            low_memory=low_memory
        )
    finally:
        for _ in workers:
            insertion_queue.put(None)

        for t in workers:
            t.join()

        if "bar" in shared_progress:
            shared_progress["bar"].close()

        print_log("CARGA DE DADOS CONCLUÍDA", level="success")

    return {
        "records_inserted": int(shared_progress.get("inserted_total", 0)),
        "failed_batches": int(shared_progress.get("failed_batches", 0)),
        "failed_rows": int(shared_progress.get("failed_rows", 0)),
        "dead_letter_files": list(shared_progress.get("dead_letter_files", [])),
    }
