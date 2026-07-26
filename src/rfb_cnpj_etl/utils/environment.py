# utils/environment.py

"""
Identificação do ambiente de execução e do banco alvo.

Serve para responder, olhando o dashboard, a pergunta prática de quem opera
mais de uma máquina: *onde* esta carga está rodando, *como* (container ou
Python direto) e *contra qual* banco.

Nada aqui é sensível por desenho: senha nunca é coletada, e o que sobra
(hostname, IP interno, versão do PostgreSQL) já está visível para quem tem
acesso ao dashboard autenticado.
"""

import os
import platform
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from .logger import print_log


def _ip_local() -> Optional[str]:
    """IP da interface que o SO usaria para sair da máquina.

    O `connect` num socket UDP **não envia pacote algum** — apenas faz o
    kernel escolher a rota e preencher o endereço de origem. É o jeito
    confiável de descobrir o IP útil numa máquina com várias interfaces
    (`gethostbyname` costuma devolver 127.0.0.1 em container).
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("192.0.2.1", 9))     # TEST-NET-1 (RFC 5737): nunca roteável
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        if s is not None:
            s.close()


def _detectar_runtime() -> Dict[str, Any]:
    """Distingue container de execução direta, e identifica o orquestrador."""
    em_docker = Path("/.dockerenv").exists()
    container_id = None
    orquestrador = None

    if os.getenv("KUBERNETES_SERVICE_HOST"):
        orquestrador = "kubernetes"

    # cgroup v1 traz o id do container no caminho; v2 costuma não trazer,
    # por isso ele é complemento e não a checagem principal.
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        if any(marca in cgroup for marca in ("docker", "containerd", "kubepods")):
            em_docker = True
        for linha in cgroup.splitlines():
            partes = linha.strip().split("/")
            if partes and len(partes[-1]) == 64 and all(c in "0123456789abcdef" for c in partes[-1]):
                container_id = partes[-1][:12]
                break
    except OSError:
        pass

    if em_docker and container_id is None:
        # Em container o hostname é, por padrão, o id curto.
        nome = socket.gethostname()
        if len(nome) == 12 and all(c in "0123456789abcdef" for c in nome):
            container_id = nome

    return {
        "runtime": "docker" if em_docker else "python",
        "container_id": container_id,
        "orquestrador": orquestrador,
    }


def collect_environment() -> Dict[str, Any]:
    """Retorna o retrato do ambiente. Nunca levanta exceção."""
    try:
        info: Dict[str, Any] = {
            "hostname": socket.gethostname(),
            "ip": _ip_local(),
            "python": platform.python_version(),
            "so": f"{platform.system()} {platform.release()}",
            "arquitetura": platform.machine(),
            "cpus": os.cpu_count(),
            "pid": os.getpid(),
        }
        info.update(_detectar_runtime())
        return info
    except Exception as exc:      # ambiente é informativo; nunca bloqueia
        print_log(f"NÃO FOI POSSÍVEL COLETAR DADOS DO AMBIENTE: {exc}", level="warning")
        return {}


def collect_database_info(postgres_config: Dict[str, Any]) -> Dict[str, Any]:
    """Dados do banco alvo: destino, versão e tamanho atual.

    A senha da configuração **não** é lida. Retorna dict vazio se o banco não
    responder — o pipeline pode estar numa etapa que nem usa banco.
    """
    info: Dict[str, Any] = {
        "host": postgres_config.get("host"),
        "porta": postgres_config.get("port"),
        "database": postgres_config.get("database"),
        "usuario": postgres_config.get("user"),
    }
    try:
        import psycopg2
        conn = psycopg2.connect(**postgres_config, connect_timeout=5)
    except Exception:
        info["acessivel"] = False
        return info

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SHOW server_version;")
            info["versao"] = f"PostgreSQL {cur.fetchone()[0]}"
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
            info["tamanho"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();")
            info["conexoes"] = int(cur.fetchone()[0])
        info["acessivel"] = True
    except Exception:
        info["acessivel"] = False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return info
