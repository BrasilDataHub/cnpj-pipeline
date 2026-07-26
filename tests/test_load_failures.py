#!/usr/bin/env python3
"""
Failure-handling tests for the load path against a real PostgreSQL.

Covers the audit fixes of 2026-07:
- a COPY batch that permanently fails is retried, isolated in the dead-letter
  directory and documented in the step metadata — but it does NOT sink the
  run (bad source batches shipped by the RFB are outside our control);
- the dead-letter cycle closes: `db dead-letter` lists the parked batches and
  `db dead-letter --retry` reloads them after a manual fix;
- FK creation failures are accumulated and fail the step at the end, while
  the other FKs are still created (the 07/2026 incident mode);
- index creation failures fail the step at the end, after every index was
  attempted;
- validation-style errors exit the CLI with a nonzero code (cron/CI).

Run (starts and tears down a disposable Docker Postgres):
    python3 tests/test_load_failures.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Must be set BEFORE importing the package: config reads it at import time.
DEAD_LETTER_DIR = Path(tempfile.mkdtemp(prefix="cnpj-dead-letter-"))
os.environ["PIPELINE_DEAD_LETTER_DIR"] = str(DEAD_LETTER_DIR)

import psycopg2  # noqa: E402

from rfb_cnpj_etl.config import POSTGRES  # noqa: E402
from rfb_cnpj_etl.orchestrator import run_orchestrator  # noqa: E402
from rfb_cnpj_etl.db.postgres_builder import PostgresBuilder  # noqa: E402
from rfb_cnpj_etl.utils.run_state import RunState, STEP_LOAD, STATUS_FAILED  # noqa: E402

PASS = FAIL = 0
CONTAINER = "cnpj-loadfail-test"
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
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER,
         "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_DB=loadtest",
         "-P", "postgres:17-alpine"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ! docker run falhou: {r.stderr.strip()}")
        return None
    port = subprocess.run(["docker", "port", CONTAINER, "5432/tcp"],
                          capture_output=True, text=True).stdout.strip().rsplit(":", 1)[-1]
    for _ in range(60):
        try:
            psycopg2.connect(
                f"postgresql://postgres:{PASSWORD}@127.0.0.1:{port}/loadtest").close()
            return port
        except psycopg2.Error:
            time.sleep(1)
    return None


def make_cnaes_zip(target_dir: Path, rows):
    """Builds a minimal RFB-style Cnaes ZIP (latin1, ';'-separated, quoted)."""
    content = "\n".join(f'"{code}";"{name}"' for code, name in rows)
    with zipfile.ZipFile(target_dir / "Cnaes.zip", "w") as z:
        z.writestr("K3241.K03200$Z.D00000.CNAECSV", content.encode("latin1"))


print("subindo Postgres efêmero…")
pg_port = start_postgres()
if not pg_port:
    print("SKIP: Docker indisponível")
    sys.exit(0)

pg_config = {
    "host": "127.0.0.1", "port": int(pg_port), "user": "postgres",
    "password": PASSWORD, "database": "loadtest",
}
# run_orchestrator reads the global POSTGRES config; point it (in place, the
# dict object is shared by the modules) at the ephemeral container so the test
# NEVER touches a real database.
POSTGRES.update(pg_config)
state_dir = Path(tempfile.mkdtemp(prefix="cnpj-loadfail-state-"))
good_dir = Path(tempfile.mkdtemp(prefix="cnpj-zips-good-"))
bad_dir = Path(tempfile.mkdtemp(prefix="cnpj-zips-bad-"))

try:
    # ------------------------------------------------------------ healthy load
    print("\nHealthy load — counting and success")
    make_cnaes_zip(good_dir, [
        ("0111301", "Cultivo de arroz"),
        ("0111302", "Cultivo de milho"),
        ("0111303", "Cultivo de trigo"),
    ])
    state_ok = RunState.load_or_create("2026-07", state_dir=state_dir)
    metrics = run_orchestrator(
        command="load", db_name="loadtest", month_year="07/2026",
        files_dir=str(good_dir), skip_validation=True, only_data=True,
        parallel=False, state=state_ok,
    )
    check("load returns the inserted-record count",
          metrics["records_inserted"] == 3, f"(got: {metrics})")
    check("data_load step marked success", state_ok.is_done(STEP_LOAD))
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cnae;")
        check("rows actually landed in the table", cur.fetchone()[0] == 3)

    # ------------------------------------------------- batch that always fails
    print("\nPoisoned batch — retry, isolate, document, but DO NOT sink the run")
    make_cnaes_zip(bad_dir, [
        ("0111301", "Cultivo de arroz"),
        ("9999999", ""),   # empty nome_cnae -> NULL -> violates NOT NULL
    ])
    state_bad = RunState.load_or_create("2026-08", state_dir=state_dir)
    captured = StringIO()
    with redirect_stdout(captured):
        bad_metrics = run_orchestrator(
            command="load", db_name="loadtest", month_year="08/2026",
            files_dir=str(bad_dir), skip_validation=True, only_data=True,
            parallel=False, state=state_bad,
        )
    output = captured.getvalue()

    check("incomplete load DOES NOT raise (the pipeline moves on)",
          bad_metrics is not None and bad_metrics["records_inserted"] == 0,
          f"(got: {bad_metrics})")
    check("the batch was retried before being given up on",
          "RETENTANDO" in output)
    check("loss is announced loudly in the log",
          "CARGA CONCLUÍDA COM PERDAS" in output and "db dead-letter --retry" in output)
    load_step = state_bad._step(STEP_LOAD)
    check("data_load step completes as success (loss is not a step failure)",
          load_step["status"] != STATUS_FAILED and state_bad.is_done(STEP_LOAD))
    check("loss documented in the step metadata (state JSON / dashboard)",
          load_step["metadata"].get("failed_batches") == 1
          and load_step["metadata"].get("failed_rows") == 2
          and len(load_step["metadata"].get("dead_letter_files", [])) == 1,
          f"(metadata: {load_step['metadata']})")
    dead_letters = sorted(DEAD_LETTER_DIR.glob("cnae-*.csv"))
    check("failed batch preserved in the dead-letter directory",
          len(dead_letters) == 1, f"(found: {[p.name for p in dead_letters]})")
    if dead_letters:
        payload = dead_letters[0].read_bytes().decode("windows-1252")
        check("dead-letter contains the batch rows", "0111301" in payload)
        meta = (dead_letters[0].with_suffix(".meta")).read_text()
        check("dead-letter .meta identifies table and source file",
              "table=cnae" in meta and "Cnaes.zip" in meta)
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cnae;")
        check("failed batch inserted nothing (no partial rows)",
              cur.fetchone()[0] == 0)

    # ---------------------------------------- dead-letter: list, fix, reload
    print("\nDead-letter cycle — list, manual fix, reload via CLI")
    cli_env = {**os.environ, "POSTGRES_HOST": "127.0.0.1",
               "POSTGRES_PORT": str(pg_port), "POSTGRES_USER": "postgres",
               "POSTGRES_PASSWORD": PASSWORD, "POSTGRES_DBNAME": "loadtest"}

    r_list = subprocess.run(
        [sys.executable, "etl.py", "db", "dead-letter", "--dir", str(DEAD_LETTER_DIR)],
        cwd=ROOT, capture_output=True, text=True, timeout=120, env=cli_env,
    )
    check("`db dead-letter` lists the parked batch",
          r_list.returncode == 0 and "cnae" in r_list.stdout
          and "LOTE(S) EM DEAD-LETTER" in r_list.stdout,
          f"(rc={r_list.returncode})\n{r_list.stdout[-400:]}")

    # A retry WITHOUT fixing must fail (and keep the file in place)...
    r_retry_bad = subprocess.run(
        [sys.executable, "etl.py", "db", "dead-letter", "--retry",
         "--db-name", "loadtest", "--dir", str(DEAD_LETTER_DIR)],
        cwd=ROOT, capture_output=True, text=True, timeout=120, env=cli_env,
    )
    check("retry of a still-broken batch exits nonzero and keeps the file",
          r_retry_bad.returncode == 1 and len(list(DEAD_LETTER_DIR.glob("cnae-*.csv"))) == 1,
          f"(rc={r_retry_bad.returncode})")

    # ...then the manual intervention: fix the bad row inside the CSV
    # (the dead-letter payload uses minimal quoting: `9999999;` = empty name).
    csv_path = sorted(DEAD_LETTER_DIR.glob("cnae-*.csv"))[0]
    content = csv_path.read_bytes().decode("windows-1252")
    fixed = content.replace("9999999;", "9999999;CNAE corrigido", 1)
    check("test fixture found the broken row to fix", fixed != content)
    csv_path.write_bytes(fixed.encode("windows-1252"))

    r_retry_ok = subprocess.run(
        [sys.executable, "etl.py", "db", "dead-letter", "--retry",
         "--db-name", "loadtest", "--dir", str(DEAD_LETTER_DIR)],
        cwd=ROOT, capture_output=True, text=True, timeout=120, env=cli_env,
    )
    check("retry after the manual fix succeeds",
          r_retry_ok.returncode == 0 and "CARREGADO" in r_retry_ok.stdout,
          f"(rc={r_retry_ok.returncode})\n{r_retry_ok.stdout[-400:]}")
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cnae;")
        check("reprocessed batch landed in the table", cur.fetchone()[0] == 2)
    check("reprocessed files moved to processed/ (audit trail kept)",
          not list(DEAD_LETTER_DIR.glob("cnae-*.csv"))
          and len(list((DEAD_LETTER_DIR / "processed").glob("cnae-*.csv"))) == 1)

    # -------------------------------------------------------------- FK failures
    print("\nForeign keys — accumulate errors, fail at the end")
    builder = PostgresBuilder(config=pg_config)
    # PKs first, mirroring the real pipeline order (FKs reference them).
    builder.add_primary_keys()
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO socio (cnpj_basico, identificador_socio, cod_qualificacao_socio,
                               data_entrada_sociedade, cod_faixa_etaria, cod_pais)
            VALUES ('00000000', '1', '99', '2020-01-01', '1', '999');
        """)
        conn.commit()

    fk_error = None
    try:
        builder.enable_foreign_keys()
    except RuntimeError as exc:
        fk_error = exc
    check("orphan rows make the FK step fail",
          fk_error is not None and "fk_socio" in str(fk_error),
          f"(got: {fk_error!r})")
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("SELECT conname FROM pg_constraint WHERE contype='f';")
        constraints = {c for (c,) in cur.fetchall()}
    check("the other FKs were still created (errors accumulated, not abort-on-first)",
          "fk_estabelecimento_1" in constraints, f"(created: {sorted(constraints)[:8]}…)")

    # ------------------------------------------------------------ index failures
    print("\nIndexes — attempt everything, fail at the end")
    with psycopg2.connect(**pg_config) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP TABLE socio CASCADE;")

    index_error = None
    captured_idx = StringIO()
    try:
        with redirect_stdout(captured_idx):
            builder.create_indexes(parallel=True, max_workers=4)
    except RuntimeError as exc:
        index_error = exc
    check("missing table makes the index step fail",
          index_error is not None and "idx_socio" in str(index_error),
          f"(got: {index_error!r})")
    with psycopg2.connect(**pg_config) as conn, conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public';")
        indexes = {i for (i,) in cur.fetchall()}
    check("the other indexes were still created",
          "idx_empresa_cnpj" in indexes and "idx_estab_cnae_estado" in indexes,
          f"(total: {len(indexes)})")
    check("indexes are built WITHOUT CONCURRENTLY (no readers during the load)",
          "CONCURRENTLY" not in captured_idx.getvalue())

    # ----------------------------------------------------------- CLI exit code
    print("\nCLI — validation failures exit nonzero")
    r = subprocess.run(
        [sys.executable, "etl.py", "db", "load", "--month", "07/2026",
         "--download-dir", "/caminho/que/nao/existe", "--no-state"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "POSTGRES_HOST": "127.0.0.1",
             "POSTGRES_PORT": str(pg_port), "POSTGRES_PASSWORD": PASSWORD,
             "POSTGRES_DBNAME": "loadtest"},
    )
    check("missing folder exits with code 1", r.returncode == 1,
          f"(rc={r.returncode})\n{(r.stdout + r.stderr)[-500:]}")
    check("error message stays in Portuguese",
          "PASTA NÃO ENCONTRADA" in (r.stdout + r.stderr))

finally:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    for d in (state_dir, good_dir, bad_dir, DEAD_LETTER_DIR):
        shutil.rmtree(d, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
