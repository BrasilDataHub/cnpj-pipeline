# utils/logger.py

"""
Logger para o projeto.
"""

from datetime import datetime
import os
import threading

# lock para evitar conflito de prints em multithread
print_lock = threading.Lock()

# momento em que a aplicação começou (para calcular tempo decorrido)
start_time = datetime.now()

# arquivo de log (opcional)
_log_file_handle = None
_log_file_path = None


def set_log_file(path: str) -> None:
    """
    Define o arquivo de log para escrita (append).
    :params:
        path: caminho do arquivo de log
    """
    global _log_file_handle, _log_file_path

    if not path:
        return

    try:
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        _log_file_handle = open(path, "a", encoding="utf-8", buffering=1)
        _log_file_path = path
    except Exception as exc:
        # evita recursão com print_log
        print(f"⚠️  Não foi possível abrir arquivo de log '{path}': {exc}")


def get_timestamp():
    """
    Retorna o timestamp atual e o tempo decorrido desde o início da aplicação.
    """
    now = datetime.now()
    elapsed = now - start_time
    hours, remainder = divmod(elapsed.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    formatted_elapsed = f"{hours:02}:{minutes:02}:{seconds:02}"
    return now.strftime("%H:%M:%S"), formatted_elapsed


def print_log(msg: str, level: str = None, time: bool = True) -> None:
    """
    Imprime uma mensagem no terminal.
    :params:
        msg: mensagem a ser impressa
        level: nível da mensagem (docs, warning, error, debug)
        time: se True, imprime o tempo decorrido desde o início da aplicação
    :return: None
    """
    now, elapsed = get_timestamp()

    emojis = {
        "success": "✅",
        "docs": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "debug": "🐞",
        "start": "🚀",
        "done": "🏁",
        "search": "🔍",
        "web": "🌐",
        "folder": "📁",
        "task": "📋",
    }

    emoji = emojis.get(level, "")

    if time:
        formatted_msg = f"🕒 {now} |⏱️ {elapsed} |{emoji} {msg}"
    else:
        formatted_msg = f"{emoji} {msg}"

    with print_lock:
        print(formatted_msg)
        if _log_file_handle is not None:
            try:
                _log_file_handle.write(formatted_msg + "\n")
            except Exception as exc:
                # evita recursão e mantém stdout intacto
                print(f"⚠️  Falha ao escrever no log '{_log_file_path}': {exc}")
