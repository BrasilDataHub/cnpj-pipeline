#!/usr/bin/env python3
"""
State, resume, webhook and dashboard tests.

Run: python3 tests/test_observability.py

No database or external network needed: the webhook points to a disposable
local HTTP server and the state uses a temporary directory.
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
    PIPELINE_STEPS, STEP_LOAD, STEP_VIEWS, STATUS_SUCCESS, STATUS_FAILED,
)
from rfb_cnpj_etl.utils.webhook import WebhookNotifier  # noqa: E402
from rfb_cnpj_etl.utils.environment import (  # noqa: E402
    collect_environment, collect_database_info,
)
from rfb_cnpj_etl.utils.dashboard import start_dashboard  # noqa: E402

PASS = FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


# ---------------------------------------------------------------- normalization
print("\nnormalize_reference_period")
check("MM/YYYY -> YYYY-MM", normalize_reference_period("07/2026") == "2026-07")
check("month without leading zero", normalize_reference_period("7/2026") == "2026-07")
check("already normalized is idempotent", normalize_reference_period("2026-07") == "2026-07")
check("None stays None", normalize_reference_period(None) is None)


# ---------------------------------------------------------------------- state
print("\nRunState — lifecycle and resume")
tmp = Path(tempfile.mkdtemp(prefix="cnpj-state-"))
try:
    st = RunState.load_or_create("2026-07", state_dir=tmp)
    check("creates the period file", (tmp / "pipeline_state_2026-07.json").exists())
    check("file name uses the period, not today's date",
          st.path.name == "pipeline_state_2026-07.json")
    check("all steps start pending",
          all(s["status"] == "pending" for s in st.data["steps"]))
    check("has run_id and created_at with offset",
          bool(st.run_id) and "+" in st.data["created_at"] or "-" in st.data["created_at"][10:])

    # run two steps
    executed = []
    run_step(st, STEP_LOAD, lambda: executed.append(STEP_LOAD))
    check("executed step becomes success", st.is_done(STEP_LOAD))

    # step failure
    def blow_up():
        raise RuntimeError("estouro simulado")

    try:
        run_step(st, STEP_VIEWS, blow_up)
    except RuntimeError:
        pass
    check("failing step becomes failed",
          st._step(STEP_VIEWS)["status"] == STATUS_FAILED)
    check("error is recorded", "estouro simulado" in (st._step(STEP_VIEWS)["error"] or ""))
    check("whole pipeline marked failed", st.data["status"] == "failed")

    # --- resume: a new instance reads the same period
    st2 = RunState.load_or_create("2026-07", state_dir=tmp)
    check("resume preserves the run_id", st2.run_id == st.run_id)
    check("resume recognizes the completed step", st2.is_done(STEP_LOAD))

    executed.clear()
    run_step(st2, STEP_LOAD, lambda: executed.append("SHOULD NOT RUN"))
    check("completed step is SKIPPED on resume", executed == [],
          f"(ran: {executed})")

    run_step(st2, STEP_VIEWS, lambda: executed.append(STEP_VIEWS))
    check("failed step is RETRIED", executed == [STEP_VIEWS])
    check("attempts are counted", st2._step(STEP_VIEWS)["attempts"] == 2)

    # --- the window belongs to the data, not the clock
    st3 = RunState.load_or_create("2026-07", state_dir=tmp)
    check("a run on another day continues in the same period state",
          st3.run_id == st.run_id and st3.is_done(STEP_LOAD))

    # --- force
    old_run_id = st3.run_id
    st4 = RunState.load_or_create("2026-07", state_dir=tmp, force=True)
    check("--force generates a new run_id", st4.run_id != old_run_id)
    check("--force resets the steps",
          all(s["status"] == "pending" for s in st4.data["steps"]))
    backups = list(tmp.glob("pipeline_state_2026-07.json.bak-*"))
    check("--force preserves a backup of the previous state", len(backups) == 1,
          f"(found: {[b.name for b in backups]})")

    # --- attempt limit
    st5 = RunState.load_or_create("2026-08", state_dir=tmp, max_attempts=2)
    for _ in range(2):
        try:
            run_step(st5, STEP_VIEWS, blow_up)
        except RuntimeError:
            pass
    try:
        run_step(st5, STEP_VIEWS, blow_up)
        check("aborts after max-attempts", False, "(did not raise)")
    except RuntimeError as exc:
        check("aborts after max-attempts with actionable instruction",
              "--force" in str(exc))

    # --- corrupted state must not bring the pipeline down
    (tmp / "pipeline_state_2026-09.json").write_text("{lixo nao json", encoding="utf-8")
    st6 = RunState.load_or_create("2026-09", state_dir=tmp)
    check("corrupted state is recreated instead of crashing",
          len(st6.data["steps"]) == len(PIPELINE_STEPS))

    # --- without state, run_step just executes
    called = []
    run_step(None, STEP_LOAD, lambda: called.append(1))
    check("without state, run_step executes normally", called == [1])

    # --- latest_period
    check("latest_period finds the most recent", RunState.latest_period(tmp) is not None)

    # --- progress inside a step (load/download)
    st7 = RunState.load_or_create("2026-11", state_dir=tmp)
    st7.start(STEP_LOAD)
    st7.progress(STEP_LOAD, current_table="empresa", records_inserted=1000, percent=1.0)
    meta = st7._step(STEP_LOAD)["metadata"]
    check("progress publishes step metadata",
          meta.get("current_table") == "empresa" and meta.get("records_inserted") == 1000)

    # throttle: consecutive calls accumulate in memory without writing each time
    mtime_before = st7.path.stat().st_mtime
    for i in range(200):
        st7.progress(STEP_LOAD, records_inserted=2000 + i)
    check("progress accumulates in memory (write throttle)",
          st7._step(STEP_LOAD)["metadata"]["records_inserted"] == 2199)
    check("throttle avoided 200 writes", st7.path.stat().st_mtime == mtime_before,
          "(wrote on every call)")

    # after the interval passes, the next call persists
    st7._last_progress_write -= 10
    st7.progress(STEP_LOAD, records_inserted=9999)
    import json as _j
    check("after the interval, progress is persisted",
          _j.loads(st7.path.read_text())["steps"][3]["metadata"]["records_inserted"] == 9999)

    # --- environment and database in the state
    st8 = RunState.load_or_create("2026-12", state_dir=tmp)
    st8.set_environment({"hostname": "maq", "runtime": "docker"}, {"database": "dados_cnpj"})
    saved = _j.loads(st8.path.read_text())
    check("state stores the environment", saved["environment"]["runtime"] == "docker")
    check("state stores the database", saved["database"]["database"] == "dados_cnpj")

    # db_info_fn is called at step completion (database "growing")
    calls = []
    st9 = RunState.load_or_create("2027-01", state_dir=tmp,
                                  db_info_fn=lambda: (calls.append(1), {"size": f"{len(calls)} GB"})[1])
    run_step(st9, STEP_LOAD, lambda: None)
    check("db_info_fn refreshes the database block at step end",
          st9.data.get("database", {}).get("size") == "1 GB")

    # a collector that blows up must not take the step down
    def _collector_blow_up():
        raise RuntimeError("banco fora do ar")
    st10 = RunState.load_or_create("2027-02", state_dir=tmp, db_info_fn=_collector_blow_up)
    run_step(st10, STEP_LOAD, lambda: None)
    check("failing database collector does not break the step", st10.is_done(STEP_LOAD))

finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- environment
print("\nExecution environment")
env_info = collect_environment()
check("detects hostname", bool(env_info.get("hostname")))
check("reports the runtime", env_info.get("runtime") in ("docker", "python"))
check("brings the Python version", env_info.get("python", "").count(".") >= 1)
check("brings OS and architecture", bool(env_info.get("os")) and bool(env_info.get("arch")))
check("brings PID and CPUs", isinstance(env_info.get("pid"), int) and isinstance(env_info.get("cpus"), int))
check("IP is an IPv4 or None",
      env_info.get("ip") is None or env_info["ip"].count(".") == 3)

db_offline = collect_database_info({"host": "127.0.0.1", "port": 1, "user": "x",
                                    "password": "y", "database": "z"})
check("unreachable database raises no exception", db_offline.get("reachable") is False)
check("target info is preserved even offline",
      db_offline.get("database") == "z" and db_offline.get("port") == 1)
check("password is NEVER collected", "password" not in db_offline and "senha" not in db_offline)


# -------------------------------------------------------------------- webhooks
print("\nWebhookNotifier")
received = []


class _Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        return


srv = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
threading.Thread(target=srv.serve_forever, daemon=True).start()
hook_port = srv.server_address[1]

tmp2 = Path(tempfile.mkdtemp(prefix="cnpj-wh-"))
try:
    notifier = WebhookNotifier(url=f"http://127.0.0.1:{hook_port}/hook")
    st = RunState.load_or_create("2026-07", state_dir=tmp2, notifier=notifier)
    st.pipeline_started()
    run_step(st, STEP_LOAD, lambda: None)
    st.pipeline_finished()

    events = [e["event"] for e in received]
    check("emits pipeline_started", "pipeline_started" in events)
    check("emits step_started", "step_started" in events)
    check("emits step_completed", "step_completed" in events)
    check("emits pipeline_completed", "pipeline_completed" in events)

    step_ev = next(e for e in received if e["event"] == "step_completed")
    check("payload has the contract keys",
          set(step_ev) == {"event", "run_id", "reference_period", "step", "timestamp"},
          f"(got: {sorted(step_ev)})")
    check("payload step has the contract keys",
          set(step_ev["step"]) == {"name", "status", "started_at", "finished_at", "error"},
          f"(got: {sorted(step_ev['step'])})")
    check("step status is success", step_ev["step"]["status"] == STATUS_SUCCESS)
    check("step name is the public English identifier",
          step_ev["step"]["name"] == STEP_LOAD)

    # --- resilience: an unreachable target must not bring the pipeline down
    bad = WebhookNotifier(url="http://127.0.0.1:9/inexistente", timeout=1)
    st_r = RunState.load_or_create("2026-10", state_dir=tmp2, notifier=bad)
    had_error = False
    try:
        st_r.pipeline_started()
        run_step(st_r, STEP_LOAD, lambda: None)
        st_r.pipeline_finished()
    except Exception:
        had_error = True
    check("unreachable webhook does NOT interrupt the pipeline", not had_error)
    check("step completed even with the webhook failing", st_r.is_done(STEP_LOAD))

    # --- flag takes priority over the env var
    import os
    os.environ["PIPELINE_WEBHOOK_URL"] = "http://env.example"
    n1 = WebhookNotifier.from_config("http://flag.example")
    check("flag takes priority over env", n1.url == "http://flag.example")
    n2 = WebhookNotifier.from_config(None)
    check("env is used when there is no flag", n2.url == "http://env.example")
    del os.environ["PIPELINE_WEBHOOK_URL"]
    check("without a url, there is no notifier", WebhookNotifier.from_config(None) is None)

    # ---------------------------------------------------------------- dashboard
    print("\nDashboard — authentication")
    import base64

    def get(port_, route, user=None, password=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port_}{route}")
        if user is not None:
            cred = base64.b64encode(f"{user}:{password}".encode()).decode()
            req.add_header("Authorization", "Basic " + cred)
        return urllib.request.urlopen(req, timeout=5)

    dash = start_dashboard(state_path=st.path, port=0, host="127.0.0.1",
                           password="senha-secreta", user="pipeline")
    check("starts without error", dash is not None)
    if dash:
        p = dash.server_address[1]

        # no credentials -> 401 with challenge
        try:
            get(p, "/")
            check("no credentials returns 401", False, "(let it through!)")
        except urllib.error.HTTPError as e:
            check("no credentials returns 401", e.code == 401)
            check("sends WWW-Authenticate (browser prompt)",
                  "Basic" in (e.headers.get("WWW-Authenticate") or ""))

        # wrong password -> 401
        try:
            get(p, "/", "pipeline", "errada")
            check("wrong password returns 401", False, "(let it through!)")
        except urllib.error.HTTPError as e:
            check("wrong password returns 401", e.code == 401)

        # wrong user -> 401
        try:
            get(p, "/", "outro", "senha-secreta")
            check("wrong user returns 401", False, "(let it through!)")
        except urllib.error.HTTPError as e:
            check("wrong user returns 401", e.code == 401)

        # the JSON is protected too
        try:
            get(p, "/state.json")
            check("state.json requires credentials", False, "(state leaked!)")
        except urllib.error.HTTPError as e:
            check("state.json requires credentials", e.code == 401)

        # correct credentials
        html = get(p, "/", "pipeline", "senha-secreta").read().decode()
        check("with credentials serves the HTML", "<html" in html.lower())
        check("uses Alpine.js via CDN", "alpinejs" in html and "cdn.jsdelivr.net" in html)
        check("CSS is inline (no external stylesheet)",
              "stylesheet" not in html and "<style>" in html)
        check("has a refresh interval selector", 'x-model.number="interval"' in html)
        check("default interval is 6s", ">6s<" in html)

        # --- not indexable (meta + header, the latter also covers state.json)
        check("meta robots with noindex",
              'name="robots"' in html and "noindex" in html)
        r_html = get(p, "/", "pipeline", "senha-secreta")
        check("X-Robots-Tag header on the HTML",
              "noindex" in (r_html.headers.get("X-Robots-Tag") or ""))
        r_json = get(p, "/state.json", "pipeline", "senha-secreta")
        check("X-Robots-Tag header on state.json",
              "noindex" in (r_json.headers.get("X-Robots-Tag") or ""))
        check("X-Content-Type-Options: nosniff",
              r_html.headers.get("X-Content-Type-Options") == "nosniff")
        check("Referrer-Policy: no-referrer",
              r_html.headers.get("Referrer-Policy") == "no-referrer")

        # --- title and semantics
        check("has a descriptive <title>", "<title>CNPJ Pipeline" in html)
        check("tab title becomes dynamic in JS", "document.title" in html)
        check("landmarks: main/header/footer", all(
              t in html for t in ("<main", "<header", "<footer")))
        check("sections labeled with <section>", "aria-labelledby" in html)
        check("metrics in <dl>/<dt>/<dd>", "<dl" in html and "<dt>" in html and "<dd" in html)
        check("uses native <progress>", "<progress" in html)
        check("select has a <label>", 'for="interval"' in html)
        check("dynamic regions with aria-live", "aria-live" in html)
        check("respects prefers-reduced-motion", "prefers-reduced-motion" in html)
        check("has <noscript> with an alternative", "<noscript>" in html)
        check("inline favicon (no 404)", 'rel="icon"' in html)
        check("declares color-scheme", 'name="color-scheme"' in html)
        check("language pt-BR", 'lang="pt-BR"' in html)
        check("UI translates step names to Portuguese", "STEP_LABELS" in html
              and "Carga de dados" in html)
        check("UI translates statuses to Portuguese", "STATUS_LABELS" in html
              and "em execução" in html)
        state_payload = json.loads(get(p, "/state.json", "pipeline", "senha-secreta").read())
        check("serves the state JSON", state_payload["run_id"] == st.run_id)
        try:
            get(p, "/qualquer", "pipeline", "senha-secreta")
            check("404 on unknown route", False)
        except urllib.error.HTTPError as e:
            check("404 on unknown route", e.code == 404)
        dash.shutdown(); dash.server_close()

    # password generated automatically when not provided
    from rfb_cnpj_etl.utils.dashboard import generate_password
    d2 = start_dashboard(state_path=st.path, port=0, host="127.0.0.1")
    if d2:
        p2 = d2.server_address[1]
        try:
            get(p2, "/")
            check("without a password it still requires auth", False, "(came up open!)")
        except urllib.error.HTTPError as e:
            check("without a password it still requires auth", e.code == 401)
        d2.shutdown(); d2.server_close()
    check("generate_password produces a non-trivial password", len(generate_password()) >= 10)
    check("generate_password does not repeat", generate_password() != generate_password())

    # --no-auth serves openly, deliberately
    d3 = start_dashboard(state_path=st.path, port=0, host="127.0.0.1", auth=False)
    if d3:
        p3 = d3.server_address[1]
        check("--no-auth serves without credentials", get(p3, "/").status == 200)
        d3.shutdown(); d3.server_close()

    # busy port must not take anything down
    busy = start_dashboard(state_path=st.path, port=hook_port, host="127.0.0.1")
    check("busy port returns None instead of exploding", busy is None)

finally:
    srv.shutdown()
    shutil.rmtree(tmp2, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
