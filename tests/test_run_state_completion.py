#!/usr/bin/env python3
"""
Completion-gate tests: when is a run `completed`, `partial` or `failed`.

Run: python3 tests/test_run_state_completion.py

No database needed. The rule under test: `pipeline_finished()` only reports
`completed` when every completion-required step (including the materialized
views) is `success` — the site's cache treats `completed` as "data ready".
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rfb_cnpj_etl.utils.run_state import (  # noqa: E402
    RunState, COMPLETION_REQUIRED_STEPS, PIPELINE_STEPS,
    STEP_DOWNLOAD, STEP_VALIDATION, STEP_INDEXES, STEP_LOAD, STEP_VIEWS,
    STATUS_PARTIAL,
)

PASS = FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


class _Recorder:
    def __init__(self):
        self.events = []

    def send(self, event, **kwargs):
        self.events.append(event)


def make_state(tmp, period, success_steps):
    st = RunState.load_or_create(period, state_dir=tmp, notifier=_Recorder())
    for name in success_steps:
        st.start(name)
        st.success(name)
    return st


print("\nCompletion gate — required steps")
check("skippable steps are not required",
      not {STEP_DOWNLOAD, STEP_VALIDATION, STEP_INDEXES} & set(COMPLETION_REQUIRED_STEPS))
check("materialized_views is required", STEP_VIEWS in COMPLETION_REQUIRED_STEPS)
check("every required step is a real pipeline step",
      set(COMPLETION_REQUIRED_STEPS) <= set(PIPELINE_STEPS))

tmp = Path(tempfile.mkdtemp(prefix="cnpj-completion-"))
try:
    # all required steps done, skippable ones pending -> completed
    st = make_state(tmp, "2026-01", COMPLETION_REQUIRED_STEPS)
    check("is_pipeline_complete with required steps done", st.is_pipeline_complete())
    check("required-only run finishes completed", st.pipeline_finished() == "completed")
    check("state JSON records completed", st.data["status"] == "completed")
    check("webhook event is pipeline_completed",
          st.notifier.events[-1] == "pipeline_completed")

    # views pending -> partial
    st = make_state(tmp, "2026-02",
                    [s for s in COMPLETION_REQUIRED_STEPS if s != STEP_VIEWS])
    check("views pending -> partial", st.pipeline_finished() == STATUS_PARTIAL)
    check("state JSON records partial", st.data["status"] == STATUS_PARTIAL)
    check("partial still notifies pipeline_completed (event contract)",
          st.notifier.events[-1] == "pipeline_completed")

    # only the load done (e.g. `db load` alone) -> partial
    st = make_state(tmp, "2026-03", [STEP_LOAD])
    check("load-only run -> partial", st.pipeline_finished() == STATUS_PARTIAL)

    # a failed step wins over everything -> failed
    st = make_state(tmp, "2026-04", COMPLETION_REQUIRED_STEPS)
    st.start(STEP_INDEXES)
    st.fail(STEP_INDEXES, RuntimeError("estouro simulado"))
    check("failed step -> failed", st.pipeline_finished() == "failed")
    check("failed run notifies pipeline_failed",
          st.notifier.events[-1] == "pipeline_failed")

    # step_finished_at: only successful steps have a timestamp
    st = make_state(tmp, "2026-05", [STEP_VIEWS])
    check("step_finished_at of a successful step",
          st.step_finished_at(STEP_VIEWS) is not None)
    check("step_finished_at of a pending step is None",
          st.step_finished_at(STEP_LOAD) is None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n  {PASS} passaram, {FAIL} falharam\n")
sys.exit(1 if FAIL else 0)
