# utils/run_state.py

"""
Pipeline run state — fault-tolerant checkpointing and resume.

The state lives in one JSON per **data reference period** (the month of the
RFB files), not per calendar day of execution. A pipeline that downloaded the
07/2026 files on the 25th and was resumed on the 26th continues in the same
`pipeline_state_2026-07.json` — the window belongs to the data, not the clock.

Concrete motivation: on 2026-07-25 the ETL ran for 6h43 and died on the last
step (materialized views). Without state, the only way to resume was knowing
by heart which subcommand to run; a naive `complete` would have dropped the
tables and started from scratch. With state, completed steps are skipped.

Atomic writes (tmp + os.replace): a crash mid-dump cannot corrupt the file,
which is exactly what needs to be readable after a crash.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import print_log
from ..config import STATE_DIR

# Minimum interval between intra-step progress writes. The load calls the
# callback on every batch (hundreds of times); without this slack the state
# file would become disk I/O in the middle of COPY.
PROGRESS_MIN_INTERVAL_SECONDS = 2.0

# Pipeline steps, in execution order. These names are the public key of the
# state file, the dashboard and the webhooks — changing them invalidates
# previously written state files.
STEP_DOWNLOAD = "download"
STEP_VALIDATION = "file_validation"
STEP_SCHEMA = "schema_init"
STEP_LOAD = "data_load"
STEP_PATCHES = "patches"
STEP_LOGGED = "tables_logged"
STEP_PK = "primary_keys"
STEP_INDEXES = "indexes"
STEP_FK = "foreign_keys"
STEP_SEARCH = "search_table"
STEP_VIEWS = "materialized_views"

PIPELINE_STEPS: List[str] = [
    STEP_DOWNLOAD,
    STEP_VALIDATION,
    STEP_SCHEMA,
    STEP_LOAD,
    STEP_PATCHES,
    STEP_LOGGED,
    STEP_PK,
    STEP_INDEXES,
    STEP_FK,
    STEP_SEARCH,
    STEP_VIEWS,
]

# Steps that must be `success` before a run may be reported as `completed`.
# Excludes the steps a legitimate load can skip by flag (`--skip-download`,
# `--skip-validation`, `--skip-index`): requiring them would keep valid runs
# from ever completing. `materialized_views` is required on purpose — the
# site treats `completed` as "data ready", and data is not ready until the
# MVs exist (see docs/observabilidade.md).
COMPLETION_REQUIRED_STEPS: List[str] = [
    STEP_SCHEMA,
    STEP_LOAD,
    STEP_PATCHES,
    STEP_LOGGED,
    STEP_PK,
    STEP_FK,
    STEP_SEARCH,
    STEP_VIEWS,
]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Terminal run status for a run that ended without failure but with required
# steps still pending (e.g. `db load` alone, `complete --skip-views`).
STATUS_PARTIAL = "partial"

DEFAULT_MAX_ATTEMPTS = 3


def now_iso() -> str:
    """ISO 8601 timestamp with timezone offset (e.g. 2026-07-25T14:32:10-03:00).

    `astimezone()` without arguments adopts the local timezone — we never
    write a naive timestamp, because state read on another machine would
    become ambiguous.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_reference_period(month_year: Optional[str]) -> Optional[str]:
    """Converts "MM/YYYY" (CLI format) into "YYYY-MM" (state key).

    Also accepts an already-normalized "YYYY-MM", to stay idempotent.
    """
    if not month_year:
        return None
    value = str(month_year).strip()
    if "/" in value:
        mm, yyyy = value.split("/", 1)
        return f"{yyyy.strip()}-{mm.strip().zfill(2)}"
    if "-" in value:
        head, tail = value.split("-", 1)
        if len(head) == 4:
            return f"{head}-{tail.zfill(2)}"
    return value


class RunState:
    """Persistent state of a run, keyed by the data reference period."""

    def __init__(
            self,
            reference_period: str,
            state_dir: Optional[Path] = None,
            max_attempts: int = DEFAULT_MAX_ATTEMPTS,
            notifier=None,
            db_info_fn=None,
    ):
        self.reference_period = reference_period
        self.state_dir = Path(state_dir) if state_dir else STATE_DIR
        self.max_attempts = max_attempts
        self.notifier = notifier
        # Called at the end of each step to refresh the `database` block —
        # this is what makes the dashboard show the database growing during
        # the load.
        self.db_info_fn = db_info_fn
        self.path = self.state_dir / f"pipeline_state_{reference_period}.json"
        self.data: Dict[str, Any] = {}
        # The load is multi-threaded: progress comes from several workers.
        self._lock = threading.Lock()
        self._last_progress_write = 0.0

    # -- lifecycle ------------------------------------------------------

    @staticmethod
    def latest_period(state_dir: Optional[Path] = None) -> Optional[str]:
        """Period of the most recently modified state file, if any.

        Serves the standalone commands (`db fk`, `db views create`), which do
        not receive `--month`: without this, a manual resume would open a new
        state instead of continuing the period in progress.
        """
        base = Path(state_dir) if state_dir else STATE_DIR
        if not base.is_dir():
            return None
        state_files = sorted(
            base.glob("pipeline_state_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not state_files:
            return None
        stem = state_files[0].stem              # pipeline_state_2026-07
        return stem.replace("pipeline_state_", "", 1) or None

    @classmethod
    def load_or_create(
            cls,
            reference_period: str,
            state_dir: Optional[Path] = None,
            force: bool = False,
            max_attempts: int = DEFAULT_MAX_ATTEMPTS,
            notifier=None,
            db_info_fn=None,
    ) -> "RunState":
        """Loads the period's state or creates a new one.

        With `force`, the previous state is preserved as `.bak-<timestamp>`
        before being replaced — evidence of what happened in the previous run
        is never discarded without a copy.
        """
        state = cls(reference_period, state_dir=state_dir,
                    max_attempts=max_attempts, notifier=notifier,
                    db_info_fn=db_info_fn)
        state.state_dir.mkdir(parents=True, exist_ok=True)

        if force and state.path.exists():
            stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
            backup = state.path.with_suffix(f".json.bak-{stamp}")
            try:
                os.replace(state.path, backup)
                print_log(f"ESTADO ANTERIOR PRESERVADO EM: {backup.name}", level="docs")
            except OSError as exc:
                print_log(f"NÃO FOI POSSÍVEL FAZER BACKUP DO ESTADO: {exc}", level="warning")

        if not force and state.path.exists():
            if state._read():
                return state

        state._create()
        return state

    def _read(self) -> bool:
        """Reads the JSON from disk. A corrupted file must not block the pipeline."""
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print_log(
                f"ESTADO EXISTENTE ILEGÍVEL ({exc}); UM NOVO SERÁ CRIADO",
                level="warning"
            )
            return False

        # State written by an older version may not know about newer steps;
        # fill in whatever is missing instead of discarding the progress.
        existing = {s.get("name") for s in self.data.get("steps", [])}
        for name in PIPELINE_STEPS:
            if name not in existing:
                self.data.setdefault("steps", []).append(self._new_step(name))

        completed = sum(1 for s in self.data.get("steps", [])
                        if s.get("status") == STATUS_SUCCESS)
        if completed:
            print_log(
                f"ESTADO ENCONTRADO PARA {self.reference_period}: "
                f"{completed}/{len(PIPELINE_STEPS)} ETAPAS JÁ CONCLUÍDAS",
                level="docs"
            )
        return True

    def _create(self) -> None:
        self.data = {
            "run_id": str(uuid.uuid4()),
            "reference_period": self.reference_period,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "in_progress",
            "steps": [self._new_step(name) for name in PIPELINE_STEPS],
        }
        self.save()

    @staticmethod
    def _new_step(name: str) -> Dict[str, Any]:
        return {
            "name": name,
            "status": STATUS_PENDING,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "attempts": 0,
            "metadata": {},
        }

    # -- access ---------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self.data.get("run_id", "")

    def _step(self, name: str) -> Optional[Dict[str, Any]]:
        for step in self.data.get("steps", []):
            if step.get("name") == name:
                return step
        return None

    def is_done(self, name: str) -> bool:
        step = self._step(name)
        return bool(step and step.get("status") == STATUS_SUCCESS)

    def exhausted(self, name: str) -> bool:
        """True when the step has already failed too many times to be retried."""
        step = self._step(name)
        if not step or self.max_attempts <= 0:
            return False
        return (step.get("status") == STATUS_FAILED
                and int(step.get("attempts") or 0) >= self.max_attempts)

    def is_pipeline_complete(self) -> bool:
        """True when every completion-required step is `success`."""
        return all(self.is_done(name) for name in COMPLETION_REQUIRED_STEPS)

    def step_finished_at(self, name: str) -> Optional[str]:
        """`finished_at` of a step, but only if it completed successfully."""
        step = self._step(name)
        if step and step.get("status") == STATUS_SUCCESS:
            return step.get("finished_at")
        return None

    # -- transitions ------------------------------------------------------

    def start(self, name: str) -> None:
        step = self._step(name)
        if step is None:
            return
        step["status"] = STATUS_RUNNING
        step["started_at"] = now_iso()
        step["finished_at"] = None
        step["error"] = None
        step["attempts"] = int(step.get("attempts") or 0) + 1
        self.save()
        self._notify("step_started", step)

    def progress(self, name: str, **fields: Any) -> None:
        """Publishes progress *within* a running step.

        Designed to be called at high frequency (per COPY batch, per file
        downloaded): it accumulates in memory and only writes every
        `PROGRESS_MIN_INTERVAL_SECONDS`. It does not fire webhooks — that
        would be hundreds of POSTs per step.
        """
        with self._lock:
            step = self._step(name)
            if step is None:
                return
            step.setdefault("metadata", {}).update(fields)
            now = time.monotonic()
            if now - self._last_progress_write < PROGRESS_MIN_INTERVAL_SECONDS:
                return
            self._last_progress_write = now
        self.save()

    def success(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        step = self._step(name)
        if step is None:
            return
        step["status"] = STATUS_SUCCESS
        step["finished_at"] = now_iso()
        step["error"] = None
        if metadata:
            step.setdefault("metadata", {}).update(metadata)
        self._refresh_database_info()
        self.save()
        self._notify("step_completed", step)

    def _refresh_database_info(self) -> None:
        """Refreshes the `database` block (size, connections) if a collector exists."""
        if self.db_info_fn is None:
            return
        try:
            info = self.db_info_fn()
            if info:
                self.data["database"] = info
        except Exception:
            pass   # informational: must never interfere with the step

    def set_environment(self, environment: Dict[str, Any], database: Dict[str, Any]) -> None:
        """Stores the environment and database snapshot in the state."""
        if environment:
            self.data["environment"] = environment
        if database:
            self.data["database"] = database
        self.save()

    def fail(self, name: str, error: BaseException) -> None:
        step = self._step(name)
        if step is None:
            return
        step["status"] = STATUS_FAILED
        step["finished_at"] = now_iso()
        step["error"] = str(error)
        self.data["status"] = "failed"
        self.save()
        self._notify("step_failed", step)

    def skip(self, name: str) -> None:
        """Records that the step was skipped because it is already complete."""
        print_log(f"ETAPA JÁ CONCLUÍDA, PULANDO: {name}", level="docs")

    def pipeline_started(self) -> None:
        self.data["status"] = "in_progress"
        self.save()
        self._notify("pipeline_started", None)

    def pipeline_finished(self) -> str:
        """Closes the state and returns the terminal status.

        `completed` only when every completion-required step (including the
        materialized views) is `success` — the site's cache invalidation
        relies on `completed` meaning "data ready". A run that ended without
        failure but with required steps pending is `partial`.

        The webhook event stays `pipeline_completed` for both `completed` and
        `partial`: the event contract (`pipeline_started|completed|failed`)
        predates `partial`, and the distinction lives in the status field.
        """
        any_failed = any(s.get("status") == STATUS_FAILED
                         for s in self.data.get("steps", []))
        if any_failed:
            status = STATUS_FAILED
        elif self.is_pipeline_complete():
            status = "completed"
        else:
            status = STATUS_PARTIAL
        self.data["status"] = status
        self.save()
        self._notify(
            "pipeline_failed" if any_failed else "pipeline_completed", None
        )
        return status

    def pipeline_failed(self, error: BaseException) -> None:
        self.data["status"] = "failed"
        self.data["error"] = str(error)
        self.save()
        self._notify("pipeline_failed", None)

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        """Writes the JSON atomically.

        A failed save never brings the pipeline down: the state is
        instrumentation, not the load. Losing the checkpoint is bad; losing
        6 hours of ETL is worse.
        """
        self.data["updated_at"] = now_iso()
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            print_log(f"FALHA AO GRAVAR ESTADO EM {self.path}: {exc}", level="warning")

    def _notify(self, event: str, step: Optional[Dict[str, Any]]) -> None:
        if self.notifier is None:
            return
        self.notifier.send(
            event=event,
            run_id=self.run_id,
            reference_period=self.reference_period,
            step=step,
        )


def run_step(state: Optional[RunState], name: str, fn, metadata_fn=None):
    """Executes `fn` under state control.

    Without state (`state is None`), it just executes — which keeps behavior
    identical to the pre-state era when tracking is off.

    An already-completed step is skipped; a step that failed too many times
    aborts with an actionable instruction instead of repeating the same error
    forever (the cron-that-reruns-itself scenario).
    """
    if state is None:
        return fn()

    if state.is_done(name):
        state.skip(name)
        return None

    if state.exhausted(name):
        raise RuntimeError(
            f"ETAPA '{name}' JÁ FALHOU {state.max_attempts} VEZES. "
            f"Investigue o erro registrado em {state.path.name} e, para "
            f"reexecutar assim mesmo, use --force ou aumente --max-attempts."
        )

    state.start(name)
    try:
        result = fn()
    except BaseException as exc:
        state.fail(name, exc)
        raise
    metadata = None
    if metadata_fn is not None:
        try:
            metadata = metadata_fn(result)
        except Exception:
            metadata = None   # metadata is accessory; it never breaks the step
    state.success(name, metadata=metadata)
    return result
