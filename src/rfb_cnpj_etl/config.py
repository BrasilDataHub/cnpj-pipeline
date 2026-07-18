# config.py

"""
Constantes e configurações do projeto.
"""

import multiprocessing
from pathlib import Path
import os

# ---------------------------------------------------------------------------
# DIRETÓRIOS
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[2]  # diretório base do projeto
DATA_DIR = BASE_DIR / "data"  # diretório para dados (downloads e banco de dados)
ENV_PATH = BASE_DIR / ".env"  # arquivo .env na raiz do projeto


def _load_env_file(env_path: Path) -> None:
    """
    Carrega variáveis do arquivo .env local para os casos em que não há python-dotenv.
    Mantém valores existentes em os.environ e ignora linhas inválidas ou comentários.
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
    Constrói um Path a partir de uma string. Se o caminho for relativo, considera BASE_DIR.
    """
    if not value:
        return default
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (BASE_DIR / candidate)


DOWNLOAD_DIR = _make_path_from_env(os.getenv("DOWNLOAD_PATH"), DATA_DIR / "downloads")
IBGE_CSV_DIR = _make_path_from_env(os.getenv("IBGE_CSV_DIR"), DATA_DIR / "locations")
IBGE_REGIOES_CSV = IBGE_CSV_DIR / "regions.csv"
IBGE_ESTADOS_CSV = IBGE_CSV_DIR / "states.csv"
IBGE_CIDADES_CSV = IBGE_CSV_DIR / "cities.csv"

# ---------------------------------------------------------------------------
# LINKS
# ---------------------------------------------------------------------------
# [LEGADO] URL antiga da RFB — não funcional após migração para WebDAV (Nextcloud)
CNPJ_DATA_URL = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/"

# URL base WebDAV (Nextcloud) da Receita Federal — configurável via variável de ambiente
CNPJ_WEBDAV_BASE_URL = (
    os.getenv("RFB_WEBDAV_URL")
    or "https://arquivos.receitafederal.gov.br/public.php/dav/files/gn672Ad4CF8N6TK/Dados/Cadastros/CNPJ/"
)

# ---------------------------------------------------------------------------
# BANCO DE DADOS (PostgreSQL)
# ---------------------------------------------------------------------------
DEFAULT_PARALLEL = True  # paralelismo de inserção no banco de dados
DEFAULT_LOW_MEMORY = False  # habilita o uso de memória limitada para inserção no banco
AVG_COMPRESSED_LINE_SIZE_BYTES = 35  # 35 bytes/linha para estimar o total de linhas e calcular o progresso da carga de dados
BRAZIL_COUNTRY_CODES = {"105", "0105"}  # códigos RFB/IBGE que representam Brasil

BATCH_SIZE = 250_000  # número de registros por batch ao inserir no banco
BATCH_RATIO = {  # proporção para utilizar em tabelas específicas
    "estabelecimento": 0.4  # Ex.: 50_000 * 0.4 = 20_000 para a tabela estabelecimento
}
WORKER_THREADS = max(1, multiprocessing.cpu_count() - 1)  # quantidade de threads de worker para pipeline de inserção
QUEUE_SIZE = max(4, WORKER_THREADS * 2)  # tamanho da fila (back‑pressure) no pipeline inserção

# ---------------------------------------------------------------------------
# CONEXÃO POSTGRESQL
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
DOWNLOAD_CHUNK_SIZE = 8_194  # tamanho (em bytes) de cada chunk ao fazer download em streaming
DOWNLOAD_CHUNK_TIMEOUT = 60  # timeout (em segundos) para cada requisição de chunk
DOWNLOAD_MAX_RETRIES = 100  # número máximo de tentativas de download antes de falhar definitivamente
DOWNLOAD_MAX_CONCURRENTS = 10  # número de downloads simultâneos padrão
BROWSER_AGENTS = [  # lista de user‑agents rotativos para as requisições HTTP
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/103.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# PRINT_LOG
# ---------------------------------------------------------------------------
# True: 🕒 23:18:32 |⏱️ 0:06:37 |🐞  2.507.405 (  1.27%) | ESTABELECIMENTOS8.ZIP   | FILA: 22 / 22
DEBUG_LOG = False  # False: utiliza uma barra de progresso com tqdm
