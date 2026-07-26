# utils/run_state.py

"""
Estado de execução do pipeline — checkpoint e retomada tolerante a falhas.

O estado vive num JSON por **período de referência dos dados** (o mês dos
arquivos da RFB), não por data civil de execução. Um pipeline que baixou os
arquivos de 07/2026 no dia 25 e foi retomado no dia 26 continua no mesmo
`pipeline_state_2026-07.json` — a janela é do dado, não do relógio.

Motivação concreta: em 25/07/2026 o ETL rodou 6h43 e morreu na última etapa
(materialized views). Sem estado, a única forma de retomar era saber de cabeça
qual subcomando executar; um `complete` ingênuo teria derrubado as tabelas e
recomeçado do zero. Com o estado, as etapas concluídas são puladas.

Escrita atômica (tmp + os.replace): uma queda no meio do dump não corrompe o
arquivo, que é justamente o que se precisa ler depois de uma queda.
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

# Intervalo mínimo entre gravações de progresso intra-etapa. A carga chama o
# callback a cada lote (centenas de vezes); sem esta folga o estado viraria
# I/O de disco no meio do COPY.
PROGRESS_MIN_INTERVAL_SECONDS = 2.0

# Etapas do pipeline, na ordem de execução. Os nomes são a chave pública do
# estado, do dashboard e dos webhooks — mudá-los invalida estados já gravados.
STEP_DOWNLOAD = "download_arquivos"
STEP_VALIDACAO = "validacao_arquivos"
STEP_SCHEMA = "schema_init"
STEP_CARGA = "carga_dados"
STEP_PATCHES = "patches"
STEP_LOGGED = "tabelas_logged"
STEP_PK = "chaves_primarias"
STEP_INDICES = "indices"
STEP_FK = "chaves_estrangeiras"
STEP_BUSCA = "tabela_busca"
STEP_VIEWS = "materialized_views"

PIPELINE_STEPS: List[str] = [
    STEP_DOWNLOAD,
    STEP_VALIDACAO,
    STEP_SCHEMA,
    STEP_CARGA,
    STEP_PATCHES,
    STEP_LOGGED,
    STEP_PK,
    STEP_INDICES,
    STEP_FK,
    STEP_BUSCA,
    STEP_VIEWS,
]

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

DEFAULT_MAX_ATTEMPTS = 3


def now_iso() -> str:
    """Timestamp ISO 8601 com offset de fuso (ex.: 2026-07-25T14:32:10-03:00).

    `astimezone()` sem argumento adota o fuso local — nunca gravamos horário
    sem offset, porque um estado lido noutra máquina viraria ambiguidade.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_reference_period(month_year: Optional[str]) -> Optional[str]:
    """Converte "MM/AAAA" (formato do CLI) em "AAAA-MM" (chave do estado).

    Aceita também "AAAA-MM" já normalizado, para ser idempotente.
    """
    if not month_year:
        return None
    value = str(month_year).strip()
    if "/" in value:
        mm, aaaa = value.split("/", 1)
        return f"{aaaa.strip()}-{mm.strip().zfill(2)}"
    if "-" in value:
        head, tail = value.split("-", 1)
        if len(head) == 4:
            return f"{head}-{tail.zfill(2)}"
    return value


class RunState:
    """Estado persistente de uma execução, indexado pelo período dos dados."""

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
        # Chamada ao fim de cada etapa para atualizar o bloco `database` —
        # é o que faz o dashboard mostrar o banco crescendo durante a carga.
        self.db_info_fn = db_info_fn
        self.path = self.state_dir / f"pipeline_state_{reference_period}.json"
        self.data: Dict[str, Any] = {}
        # A carga é multi-thread: o progresso vem de vários workers.
        self._lock = threading.Lock()
        self._ultimo_progresso = 0.0

    # -- ciclo de vida ------------------------------------------------------

    @staticmethod
    def latest_period(state_dir: Optional[Path] = None) -> Optional[str]:
        """Período do estado modificado mais recentemente, se houver.

        Serve aos comandos avulsos (`db fk`, `db views create`), que não
        recebem `--month`: sem isto, uma retomada manual abriria um estado
        novo em vez de continuar o do período em andamento.
        """
        base = Path(state_dir) if state_dir else STATE_DIR
        if not base.is_dir():
            return None
        arquivos = sorted(
            base.glob("pipeline_state_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not arquivos:
            return None
        nome = arquivos[0].stem              # pipeline_state_2026-07
        return nome.replace("pipeline_state_", "", 1) or None

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
        """Carrega o estado do período ou cria um novo.

        Com `force`, o estado anterior é preservado como `.bak-<timestamp>`
        antes de ser substituído — nunca se descarta a evidência do que
        aconteceu na execução anterior sem deixar cópia.
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
        """Lê o JSON do disco. Um arquivo corrompido não pode travar o pipeline."""
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print_log(
                f"ESTADO EXISTENTE ILEGÍVEL ({exc}); UM NOVO SERÁ CRIADO",
                level="warning"
            )
            return False

        # Um estado gravado por uma versão anterior pode não conhecer etapas
        # novas; completa o que faltar em vez de descartar o progresso.
        existentes = {s.get("name") for s in self.data.get("steps", [])}
        for name in PIPELINE_STEPS:
            if name not in existentes:
                self.data.setdefault("steps", []).append(self._new_step(name))

        concluidas = sum(1 for s in self.data.get("steps", [])
                         if s.get("status") == STATUS_SUCCESS)
        if concluidas:
            print_log(
                f"ESTADO ENCONTRADO PARA {self.reference_period}: "
                f"{concluidas}/{len(PIPELINE_STEPS)} ETAPAS JÁ CONCLUÍDAS",
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

    # -- acesso -------------------------------------------------------------

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
        """True se a etapa já falhou vezes demais para ser retentada."""
        step = self._step(name)
        if not step or self.max_attempts <= 0:
            return False
        return (step.get("status") == STATUS_FAILED
                and int(step.get("attempts") or 0) >= self.max_attempts)

    # -- transições ---------------------------------------------------------

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

    def progress(self, name: str, **campos: Any) -> None:
        """Publica progresso *dentro* de uma etapa em andamento.

        Pensada para ser chamada com frequência alta (por lote de COPY, por
        arquivo baixado): acumula em memória e só grava a cada
        `PROGRESS_MIN_INTERVAL_SECONDS`. Não dispara webhook — seriam
        centenas de POSTs por etapa.
        """
        with self._lock:
            step = self._step(name)
            if step is None:
                return
            step.setdefault("metadata", {}).update(campos)
            agora = time.monotonic()
            if agora - self._ultimo_progresso < PROGRESS_MIN_INTERVAL_SECONDS:
                return
            self._ultimo_progresso = agora
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
        self._atualizar_banco()
        self.save()
        self._notify("step_completed", step)

    def _atualizar_banco(self) -> None:
        """Refresca o bloco `database` (tamanho, conexões) se houver coletor."""
        if self.db_info_fn is None:
            return
        try:
            info = self.db_info_fn()
            if info:
                self.data["database"] = info
        except Exception:
            pass   # informativo: jamais interfere na etapa

    def set_environment(self, ambiente: Dict[str, Any], banco: Dict[str, Any]) -> None:
        """Grava o retrato do ambiente e do banco no estado."""
        if ambiente:
            self.data["environment"] = ambiente
        if banco:
            self.data["database"] = banco
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
        """Registra que a etapa foi pulada por já estar concluída."""
        print_log(f"ETAPA JÁ CONCLUÍDA, PULANDO: {name}", level="docs")

    def pipeline_started(self) -> None:
        self.data["status"] = "in_progress"
        self.save()
        self._notify("pipeline_started", None)

    def pipeline_finished(self) -> None:
        """Fecha o estado. Só marca `completed` se toda etapa executada foi bem."""
        houve_falha = any(s.get("status") == STATUS_FAILED
                          for s in self.data.get("steps", []))
        self.data["status"] = "failed" if houve_falha else "completed"
        self.save()
        self._notify(
            "pipeline_failed" if houve_falha else "pipeline_completed", None
        )

    def pipeline_failed(self, error: BaseException) -> None:
        self.data["status"] = "failed"
        self.data["error"] = str(error)
        self.save()
        self._notify("pipeline_failed", None)

    # -- persistência -------------------------------------------------------

    def save(self) -> None:
        """Grava o JSON de forma atômica.

        Falha ao salvar nunca derruba o pipeline: o estado é instrumentação,
        não a carga. Perder o checkpoint é ruim; perder 6h de ETL é pior.
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
    """Executa `fn` sob o controle do estado.

    Sem estado (`state is None`), apenas executa — é o que mantém o
    comportamento idêntico ao anterior quando o rastreamento está desligado.

    Etapa já concluída é pulada; etapa que falhou vezes demais aborta com uma
    instrução acionável em vez de repetir o mesmo erro indefinidamente (o caso
    de um cron que reexecuta sozinho).
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
        resultado = fn()
    except BaseException as exc:
        state.fail(name, exc)
        raise
    metadata = None
    if metadata_fn is not None:
        try:
            metadata = metadata_fn(resultado)
        except Exception:
            metadata = None   # metadado é acessório; nunca quebra a etapa
    state.success(name, metadata=metadata)
    return resultado
