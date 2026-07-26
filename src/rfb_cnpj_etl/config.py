# config.py

"""
Project constants and configuration.
"""

import multiprocessing
from pathlib import Path
import os

# ---------------------------------------------------------------------------
# DIRECTORIES
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[2]  # project base directory
DATA_DIR = BASE_DIR / "data"  # data directory (downloads and database files)
ENV_PATH = BASE_DIR / ".env"  # .env file at the project root


def _load_env_file(env_path: Path) -> None:
    """
    Loads variables from the local .env file for environments without python-dotenv.
    Keeps existing os.environ values and ignores invalid lines and comments.
    """
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('\'"')
        os.environ.setdefault(key, value)


_load_env_file(ENV_PATH)


def _make_path_from_env(value: str, default: Path) -> Path:
    """
    Builds a Path from a string. Relative paths are resolved against BASE_DIR.
    """
    if not value:
        return default
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (BASE_DIR / candidate)


DOWNLOAD_DIR = _make_path_from_env(os.getenv("DOWNLOAD_PATH"), DATA_DIR / "downloads")
IBGE_CSV_DIR = _make_path_from_env(os.getenv("IBGE_CSV_DIR"), DATA_DIR / "locations")
# Run state (checkpoint/resume). One JSON per data reference period —
# see utils/run_state.py.
STATE_DIR = _make_path_from_env(os.getenv("PIPELINE_STATE_DIR"), DATA_DIR / "state")
# Batches that permanently fail during COPY are dumped here for inspection —
# the pipeline never discards data silently (see db/postgres_loader.py).
DEAD_LETTER_DIR = _make_path_from_env(
    os.getenv("PIPELINE_DEAD_LETTER_DIR"), DATA_DIR / "logs" / "dead_letter"
)
IBGE_REGIOES_CSV = IBGE_CSV_DIR / "regions.csv"
IBGE_ESTADOS_CSV = IBGE_CSV_DIR / "states.csv"
IBGE_CIDADES_CSV = IBGE_CSV_DIR / "cities.csv"

# ---------------------------------------------------------------------------
# LINKS
# ---------------------------------------------------------------------------
# [LEGACY] Old RFB URL — no longer functional after the WebDAV (Nextcloud) migration
CNPJ_DATA_URL = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"

# RFB WebDAV (Nextcloud) base URL — configurable via environment variable
CNPJ_WEBDAV_BASE_URL = (
    os.getenv("RFB_WEBDAV_URL")
    or "https://arquivos.receitafederal.gov.br/public.php/dav/files/gn672Ad4CF8N6TK/Dados/Cadastros/CNPJ/"
)

# ---------------------------------------------------------------------------
# DATABASE (PostgreSQL)
# ---------------------------------------------------------------------------
DEFAULT_PARALLEL = True  # parallel database insertion
DEFAULT_LOW_MEMORY = False  # constrained-memory mode for insertion
AVG_COMPRESSED_LINE_SIZE_BYTES = 35  # bytes/line heuristic to estimate total rows for load progress
BRAZIL_COUNTRY_CODES = {"105", "0105"}  # RFB/IBGE codes that represent Brazil

BATCH_SIZE = 250_000  # rows per batch when inserting into the database
BATCH_RATIO = {  # per-table batch size multiplier
    "estabelecimento": 0.4  # e.g. 250_000 * 0.4 = 100_000 rows per batch for `estabelecimento`
}
WORKER_THREADS = max(1, multiprocessing.cpu_count() - 1)  # insertion pipeline worker threads
QUEUE_SIZE = max(4, WORKER_THREADS * 2)  # insertion queue size (back-pressure)

# Index creation. The target database never has readers during a load (the
# website only switches to it after the pipeline finishes), so indexes are
# built WITHOUT `CONCURRENTLY` and in parallel — pure speed, no locks to fear.
# Memory note: each worker session sets INDEX_MAINTENANCE_WORK_MEM, so peak
# usage is roughly workers × work_mem; tune both on smaller hosts.
INDEX_MAX_WORKERS = int(os.getenv("INDEX_MAX_WORKERS", 4))
INDEX_MAINTENANCE_WORK_MEM = os.getenv("INDEX_MAINTENANCE_WORK_MEM", "2GB")

# ---------------------------------------------------------------------------
# POSTGRESQL CONNECTION
# ---------------------------------------------------------------------------
POSTGRES = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "sua_senha_aqui"),
    "database": os.getenv("POSTGRES_DBNAME", "dados_cnpj")
}

# ---------------------------------------------------------------------------
# DOWNLOADS
# ---------------------------------------------------------------------------
DOWNLOAD_CHUNK_SIZE = 8_192  # chunk size (bytes) for streaming downloads
DOWNLOAD_CHUNK_TIMEOUT = 60  # timeout (seconds) per chunk request
DOWNLOAD_MAX_RETRIES = 100  # maximum download attempts before failing for good
DOWNLOAD_MAX_CONCURRENTS = 10  # default number of simultaneous downloads
BROWSER_AGENTS = [  # rotating user agents for HTTP requests
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/103.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# OBSERVABILITY (state, dashboard, webhooks)
# ---------------------------------------------------------------------------
# Per-run statistics table. Preserved by drop_tables() — a reload must never
# wipe the execution history.
PIPELINE_STATS_TABLE = "pipeline_stats"

# Read-only dashboard. 3010 avoids collisions with common dev ports
# (3000, 8000, 8080).
DASHBOARD_DEFAULT_PORT = int(os.getenv("PIPELINE_PORT", 3010))
# Initial polling interval; adjustable on the page itself (persisted in the
# browser's localStorage).
DASHBOARD_REFRESH_SECONDS = int(os.getenv("PIPELINE_REFRESH_SECONDS", 6))
# Basic Auth: fixed user by default — what matters is the password, provided
# via --dashboard-password/PIPELINE_DASHBOARD_PASSWORD or generated per run.
DASHBOARD_USER = os.getenv("PIPELINE_DASHBOARD_USER", "pipeline")
DASHBOARD_PASSWORD = os.getenv("PIPELINE_DASHBOARD_PASSWORD") or None

# Attempts per step before requiring intervention (--max-attempts).
MAX_STEP_ATTEMPTS = int(os.getenv("PIPELINE_MAX_ATTEMPTS", 3))

# ---------------------------------------------------------------------------
# PRINT_LOG
# ---------------------------------------------------------------------------
# True: 🕒 23:18:32 |⏱️ 0:06:37 |🐞  2.507.405 (  1.27%) | ESTABELECIMENTOS8.ZIP   | FILA: 22 / 22
DEBUG_LOG = False  # False: uses a tqdm progress bar
