# utils/webhook.py

"""
HTTP notifications per pipeline step.

The rule that governs this whole module: **a webhook failure never interrupts
the pipeline**. The destination may be down, slow or misconfigured; none of
that can cost a load that takes hours. Hence: short timeout, no retries, and
every exception caught and logged.

Uses `urllib` from the standard library instead of `requests` on purpose — the
sending happens at critical points and it is not worth adding dependency
surface for a JSON POST.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .logger import print_log
from .run_state import now_iso

WEBHOOK_TIMEOUT_SECONDS = 5

# Emitted events. Kept as constants because they are a public contract: they
# are documented in the README and consumed by third parties.
EVENT_PIPELINE_STARTED = "pipeline_started"
EVENT_STEP_STARTED = "step_started"
EVENT_STEP_COMPLETED = "step_completed"
EVENT_STEP_FAILED = "step_failed"
EVENT_PIPELINE_COMPLETED = "pipeline_completed"
EVENT_PIPELINE_FAILED = "pipeline_failed"


class WebhookNotifier:
    """Sends pipeline events to a URL, without ever propagating an error."""

    def __init__(self, url: Optional[str] = None, timeout: int = WEBHOOK_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout
        self._failures = 0

    @classmethod
    def from_config(cls, cli_url: Optional[str] = None) -> Optional["WebhookNotifier"]:
        """Resolves the URL: the flag takes priority over the environment variable.

        Returns None when there is no URL — the caller then notifies nothing,
        which is the default behavior (webhooks are opt-in).
        """
        url = cli_url or os.getenv("PIPELINE_WEBHOOK_URL")
        if not url:
            return None
        source = "--webhook-url" if cli_url else "PIPELINE_WEBHOOK_URL"
        print_log(f"WEBHOOKS HABILITADOS VIA {source}", level="web")
        return cls(url=url)

    def send(
            self,
            event: str,
            run_id: str,
            reference_period: str,
            step: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Sends an event. Returns True if delivered; False on any failure."""
        if not self.url:
            return False

        payload: Dict[str, Any] = {
            "event": event,
            "run_id": run_id,
            "reference_period": reference_period,
            "timestamp": now_iso(),
        }

        if step is not None:
            # Only the contract fields: `metadata` and `attempts` are internal
            # details of the state and do not go into the documented payload.
            payload["step"] = {
                "name": step.get("name"),
                "status": step.get("status"),
                "started_at": step.get("started_at"),
                "finished_at": step.get("finished_at"),
                "error": step.get("error"),
            }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "cnpj-pipeline-webhook/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
                print_log(
                    f"WEBHOOK '{event}' RESPONDEU HTTP {resp.status}",
                    level="warning"
                )
                return False
        except urllib.error.HTTPError as exc:
            self._log_failure(event, f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            self._log_failure(event, f"conexão: {exc.reason}")
        except Exception as exc:  # timeout, DNS, TLS, payload — nothing escapes
            self._log_failure(event, str(exc))
        return False

    def _log_failure(self, event: str, reason: str) -> None:
        self._failures += 1
        # Log on every failure at first, then sparsely: a destination that is
        # down must not flood the log of a 6-hour load.
        if self._failures <= 3 or self._failures % 25 == 0:
            print_log(
                f"FALHA AO ENVIAR WEBHOOK '{event}' ({reason}) "
                f"— pipeline segue normalmente [falha #{self._failures}]",
                level="warning"
            )
