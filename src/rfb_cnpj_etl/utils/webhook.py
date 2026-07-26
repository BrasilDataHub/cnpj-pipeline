# utils/webhook.py

"""
Notificações HTTP por etapa do pipeline.

Regra que governa todo este módulo: **uma falha de webhook nunca interrompe o
pipeline**. O destino pode estar fora do ar, lento ou mal configurado; nada
disso pode custar uma carga de horas. Por isso: timeout curto, nenhuma
retentativa e toda exceção capturada e logada.

Usa `urllib` da biblioteca padrão em vez de `requests` de propósito — o envio
acontece em pontos críticos e não vale adicionar superfície de dependência
para um POST de JSON.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .logger import print_log
from .run_state import now_iso

WEBHOOK_TIMEOUT_SECONDS = 5

# Eventos emitidos. Mantidos como constantes porque são contrato público:
# estão documentados no README e consumidos por terceiros.
EVENT_PIPELINE_STARTED = "pipeline_started"
EVENT_STEP_STARTED = "step_started"
EVENT_STEP_COMPLETED = "step_completed"
EVENT_STEP_FAILED = "step_failed"
EVENT_PIPELINE_COMPLETED = "pipeline_completed"
EVENT_PIPELINE_FAILED = "pipeline_failed"


class WebhookNotifier:
    """Envia eventos do pipeline para uma URL, sem jamais propagar erro."""

    def __init__(self, url: Optional[str] = None, timeout: int = WEBHOOK_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout
        self._falhas = 0

    @classmethod
    def from_config(cls, cli_url: Optional[str] = None) -> Optional["WebhookNotifier"]:
        """Resolve a URL: flag tem prioridade sobre variável de ambiente.

        Retorna None quando não há URL — o chamador então não notifica nada,
        que é o comportamento padrão (webhooks são opt-in).
        """
        url = cli_url or os.getenv("PIPELINE_WEBHOOK_URL")
        if not url:
            return None
        origem = "--webhook-url" if cli_url else "PIPELINE_WEBHOOK_URL"
        print_log(f"WEBHOOKS HABILITADOS VIA {origem}", level="web")
        return cls(url=url)

    def send(
            self,
            event: str,
            run_id: str,
            reference_period: str,
            step: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Envia um evento. Retorna True se entregue; False em qualquer falha."""
        if not self.url:
            return False

        payload: Dict[str, Any] = {
            "event": event,
            "run_id": run_id,
            "reference_period": reference_period,
            "timestamp": now_iso(),
        }

        if step is not None:
            # Só os campos do contrato: `metadata` e `attempts` são detalhes
            # internos do estado e não entram no payload documentado.
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
            self._registrar_falha(event, f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            self._registrar_falha(event, f"conexão: {exc.reason}")
        except Exception as exc:  # timeout, DNS, TLS, payload — nada escapa
            self._registrar_falha(event, str(exc))
        return False

    def _registrar_falha(self, event: str, motivo: str) -> None:
        self._falhas += 1
        # Log a cada falha nas primeiras, depois esparso: um destino fora do ar
        # não pode inundar o log de uma carga de 6 horas.
        if self._falhas <= 3 or self._falhas % 25 == 0:
            print_log(
                f"FALHA AO ENVIAR WEBHOOK '{event}' ({motivo}) "
                f"— pipeline segue normalmente [falha #{self._falhas}]",
                level="warning"
            )
