# utils/environment.py

"""
Identification of the execution environment and the target database.

Answers the practical questions of whoever operates more than one machine by
just looking at the dashboard: *where* this load is running, *how* (container
or bare Python) and *against which* database.

Nothing here is sensitive by design: the password is never collected, and
what remains (hostname, internal IP, PostgreSQL version) is already visible
to anyone with access to the authenticated dashboard.
"""

import os
import platform
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from .logger import print_log


def _local_ip() -> Optional[str]:
    """IP of the interface the OS would use to leave the machine.

    `connect` on a UDP socket **sends no packet at all** — it only makes the
    kernel pick the route and fill in the source address. It is the reliable
    way to find the useful IP on a machine with several interfaces
    (`gethostbyname` tends to return 127.0.0.1 inside containers).
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("192.0.2.1", 9))     # TEST-NET-1 (RFC 5737): never routable
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        if s is not None:
            s.close()


def _detect_runtime() -> Dict[str, Any]:
    """Distinguishes container from direct execution and identifies the orchestrator."""
    in_docker = Path("/.dockerenv").exists()
    container_id = None
    orchestrator = None

    if os.getenv("KUBERNETES_SERVICE_HOST"):
        orchestrator = "kubernetes"

    # cgroup v1 carries the container id in the path; v2 usually does not,
    # which is why this is a complement and not the primary check.
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        if any(marker in cgroup for marker in ("docker", "containerd", "kubepods")):
            in_docker = True
        for line in cgroup.splitlines():
            parts = line.strip().split("/")
            if parts and len(parts[-1]) == 64 and all(c in "0123456789abcdef" for c in parts[-1]):
                container_id = parts[-1][:12]
                break
    except OSError:
        pass

    if in_docker and container_id is None:
        # Inside a container the hostname defaults to the short id.
        hostname = socket.gethostname()
        if len(hostname) == 12 and all(c in "0123456789abcdef" for c in hostname):
            container_id = hostname

    return {
        "runtime": "docker" if in_docker else "python",
        "container_id": container_id,
        "orchestrator": orchestrator,
    }


def collect_environment() -> Dict[str, Any]:
    """Returns the environment snapshot. Never raises."""
    try:
        info: Dict[str, Any] = {
            "hostname": socket.gethostname(),
            "ip": _local_ip(),
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "cpus": os.cpu_count(),
            "pid": os.getpid(),
        }
        info.update(_detect_runtime())
        return info
    except Exception as exc:      # environment is informational; never blocks
        print_log(f"NÃO FOI POSSÍVEL COLETAR DADOS DO AMBIENTE: {exc}", level="warning")
        return {}


def collect_database_info(postgres_config: Dict[str, Any]) -> Dict[str, Any]:
    """Target database info: destination, version and current size.

    The configured password is **not** read. Returns the connection block
    with `reachable: false` when the database does not respond — the pipeline
    may be at a step that does not even use the database.
    """
    info: Dict[str, Any] = {
        "host": postgres_config.get("host"),
        "port": postgres_config.get("port"),
        "database": postgres_config.get("database"),
        "user": postgres_config.get("user"),
    }
    try:
        import psycopg2
        conn = psycopg2.connect(**postgres_config, connect_timeout=5)
    except Exception:
        info["reachable"] = False
        return info

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SHOW server_version;")
            info["version"] = f"PostgreSQL {cur.fetchone()[0]}"
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
            info["size"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();")
            info["connections"] = int(cur.fetchone()[0])
        info["reachable"] = True
    except Exception:
        info["reachable"] = False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return info
