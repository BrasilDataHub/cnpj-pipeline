#!/usr/bin/env python3
"""
End-to-end test: runs the real CLI against a real PostgreSQL and verifies
state, resume, --force, webhooks, dashboard and pipeline_stats working
together.

Run: python3 tests/test_e2e_resume.py

Uses `db init`, which creates the full schema and loads the IBGE tables — it
is the cheapest real step of the pipeline, and it exercises the entire path
(main -> orchestrator -> run_step -> state -> stats).
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psycopg2  # noqa: E402

PASS = FAIL = 0
CONTAINER = "cnpj-e2e-test"
PASSWORD = "teste-local"
PERIOD = "2026-07"


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
         "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_DB=e2e",
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
            psycopg2.connect(f"postgresql://postgres:{PASSWORD}@127.0.0.1:{port}/e2e").close()
            return port
        except psycopg2.Error:
            time.sleep(1)
    return None


# ---- webhook collector -------------------------------------------------------
received = []


class _Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200); self.send_header("Content-Length", "2")
        self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        return


print("subindo Postgres efêmero…")
pg_port = start_postgres()
if not pg_port:
    print("SKIP: Docker indisponível")
    sys.exit(0)

hook = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
threading.Thread(target=hook.serve_forever, daemon=True).start()
hook_port = hook.server_address[1]

state_dir = Path(tempfile.mkdtemp(prefix="cnpj-e2e-state-"))

env = os.environ.copy()
env.update({
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": pg_port,
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": PASSWORD,
    "POSTGRES_DBNAME": "e2e",
    "PIPELINE_STATE_DIR": str(state_dir),
})


def run_cli(*extra):
    """Runs the CLI and returns (returncode, combined output)."""
    r = subprocess.run(
        [sys.executable, "etl.py", "db", "init", "--db-name", "e2e",
         "--reference-period", PERIOD, *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    return r.returncode, r.stdout + r.stderr


state_file = state_dir / f"pipeline_state_{PERIOD}.json"

try:
    # ---------------------------------------------------------- 1st run
    print("\n1st run — creates state, executes step, writes stats")
    rc, output = run_cli("--webhook-url", f"http://127.0.0.1:{hook_port}/hook")
    check("CLI finished successfully", rc == 0, f"\n{output[-1500:]}")
    check("state file created for the right period", state_file.exists())

    st = json.loads(state_file.read_text())
    check("run_id is a UUID", len(st["run_id"]) == 36)
    check("reference_period is correct", st["reference_period"] == PERIOD)
    check("created_at has a timezone offset",
          "+" in st["created_at"][10:] or "-" in st["created_at"][10:])
    schema = next(s for s in st["steps"] if s["name"] == "schema_init")
    check("schema_init step completed", schema["status"] == "success",
          f"(status: {schema['status']}, erro: {schema.get('error')})")
    check("step has started_at and finished_at",
          schema["started_at"] and schema["finished_at"])
    check("overall pipeline status", st["status"] == "completed",
          f"(got: {st['status']})")
    check("step names are the English public identifiers",
          {s["name"] for s in st["steps"]} >= {"download", "file_validation",
                                               "data_load", "foreign_keys"},
          f"(got: {[s['name'] for s in st['steps']]})")

    # --- environment and database written by the real CLI
    env_block = st.get("environment", {})
    check("state carries the execution environment", bool(env_block.get("hostname")))
    check("identifies the runtime (docker/python)", env_block.get("runtime") in ("docker", "python"))
    db_block = st.get("database", {})
    check("state carries the target database", db_block.get("database") == "e2e")
    check("state carries the PostgreSQL version", "PostgreSQL" in (db_block.get("version") or ""))
    check("state carries the database size", bool(db_block.get("size")))
    check("database password does not leak into the state",
          "password" not in json.dumps(db_block) and PASSWORD not in json.dumps(st))

    events = [e["event"] for e in received]
    check("pipeline_started webhook received", "pipeline_started" in events)
    check("step_completed webhook received", "step_completed" in events)
    check("pipeline_completed webhook received", "pipeline_completed" in events)

    conn = psycopg2.connect(f"postgresql://postgres:{PASSWORD}@127.0.0.1:{pg_port}/e2e")
    with conn.cursor() as cur:
        cur.execute("SELECT run_id::text, status, reference_period, finished_at, tables_populated "
                    "FROM pipeline_stats ORDER BY started_at DESC LIMIT 1;")
        row = cur.fetchone()
    check("pipeline_stats recorded the run", row is not None)
    if row:
        check("database run_id matches the JSON one", row[0] == st["run_id"])
        check("status recorded as completed", row[1] == "completed")
        check("finished_at filled in", row[3] is not None)
        check("tables_populated lists the loaded IBGE tables",
              row[4] is not None and len(row[4]) >= 0)

    # ---------------------------------------------------------- 2nd run
    print("\n2nd run — must SKIP the already-completed step")
    rc2, output2 = run_cli()
    check("CLI finished successfully", rc2 == 0, f"\n{output2[-1000:]}")
    check("log says the step was skipped",
          "JÁ CONCLUÍDA, PULANDO: schema_init" in output2,
          f"\n{output2[-800:]}")
    check("log says state was found",
          "ESTADO ENCONTRADO" in output2)

    st2 = json.loads(state_file.read_text())
    check("run_id preserved across runs", st2["run_id"] == st["run_id"])
    schema2 = next(s for s in st2["steps"] if s["name"] == "schema_init")
    check("skipped step keeps attempts=1 (did not re-run)",
          schema2["attempts"] == 1, f"(got: {schema2['attempts']})")
    check("finished_at unchanged", schema2["finished_at"] == schema["finished_at"])

    # ------------------------------------------------------- 3rd with --force
    print("\n3rd run — --force re-runs from scratch")
    rc3, output3 = run_cli("--force")
    check("CLI finished successfully", rc3 == 0, f"\n{output3[-1000:]}")
    check("log mentions the previous state backup",
          "ESTADO ANTERIOR PRESERVADO" in output3)
    backups = list(state_dir.glob(f"pipeline_state_{PERIOD}.json.bak-*"))
    check(".bak backup created", len(backups) == 1,
          f"({[b.name for b in backups]})")
    st3 = json.loads(state_file.read_text())
    check("--force generated a new run_id", st3["run_id"] != st["run_id"])
    schema3 = next(s for s in st3["steps"] if s["name"] == "schema_init")
    check("step re-ran (was not skipped)", schema3["status"] == "success")
    check("no 'PULANDO' appears with --force",
          "JÁ CONCLUÍDA, PULANDO" not in output3)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_stats;")
        total = cur.fetchone()[0]
    check("pipeline_stats accumulates run history", total >= 2,
          f"(rows: {total})")

    # ------------------------------------------------------- 4th with --no-state
    print("\n4th run — --no-state does not touch the file")
    before = state_file.read_text()
    rc4, output4 = run_cli("--no-state")
    check("CLI finished successfully", rc4 == 0, f"\n{output4[-1000:]}")
    check("state file unchanged", state_file.read_text() == before)
    check("default behavior preserved (no mention of state)",
          "ESTADO ENCONTRADO" not in output4)

    # ---------------------------------------------------------- dashboard
    print("\nDashboard serving the real state")
    # --force so the schema is recreated from scratch: a run that only skips
    # steps lasts ~1s and would close the dashboard before any request.
    proc = subprocess.Popen(
        [sys.executable, "etl.py", "db", "init", "--db-name", "e2e",
         "--reference-period", PERIOD, "--force", "--serve", "--port", "3117",
         "--dashboard-password", "senha-e2e"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    import urllib.request, base64
    cred = base64.b64encode(b"pipeline:senha-e2e").decode()

    def _get(route, authenticated=True):
        req = urllib.request.Request(f"http://127.0.0.1:3117{route}")
        if authenticated:
            req.add_header("Authorization", "Basic " + cred)
        return urllib.request.urlopen(req, timeout=2)

    html = state_json = None
    denied = None
    for _ in range(200):
        if proc.poll() is not None:
            break
        try:
            html = _get("/").read().decode()
            state_json = json.loads(_get("/state.json").read())
            # confirms the server refuses unauthenticated requests
            try:
                _get("/", authenticated=False)
                denied = False
            except urllib.error.HTTPError as e:
                denied = (e.code == 401)
            break
        except Exception:
            time.sleep(0.05)
    check("dashboard requires Basic Auth", denied is True, f"(denied={denied})")
    check("dashboard responded during the run", html is not None)
    if html:
        check("page has the pipeline title", "CNPJ Pipeline" in html)
        check("uses Alpine.js via CDN", "alpinejs" in html)
        check("inline CSS (no external stylesheet)",
              "stylesheet" not in html and "<style>" in html)
        check("polling with adjustable interval", "setInterval" in html and "interval" in html)
    if state_json:
        check("dashboard serves the state of the right period",
              state_json["reference_period"] == PERIOD)
    proc.wait(timeout=300)
    output5 = proc.stdout.read()
    check("run with --serve finished well", proc.returncode == 0,
          f"\n{output5[-800:]}")

    conn.close()

finally:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    hook.shutdown()
    shutil.rmtree(state_dir, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
