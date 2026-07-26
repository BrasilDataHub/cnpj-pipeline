# utils/logger.py

"""
Logger for the project.
"""

from datetime import datetime
import os
import threading

# lock to avoid print conflicts in multithread
print_lock = threading.Lock()

# moment when the application started (to compute elapsed time)
start_time = datetime.now()

# log file (optional)
_log_file_handle = None
_log_file_path = None


def set_log_file(path: str) -> None:
    """
    Sets the log file for writing (append).
    :params:
        path: path of the log file
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
        # avoids recursion with print_log
        print(f"⚠️  Não foi possível abrir arquivo de log '{path}': {exc}")


def get_timestamp():
    """
    Returns the current timestamp and the time elapsed since the application started.
    """
    now = datetime.now()
    elapsed = now - start_time
    hours, remainder = divmod(elapsed.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    formatted_elapsed = f"{hours:02}:{minutes:02}:{seconds:02}"
    return now.strftime("%H:%M:%S"), formatted_elapsed


def print_log(msg: str, level: str = None, time: bool = True) -> None:
    """
    Prints a message to the terminal.
    :params:
        msg: message to be printed
        level: message level (docs, warning, error, debug)
        time: if True, prints the time elapsed since the application started
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
                # avoids recursion and keeps stdout intact
                print(f"⚠️  Falha ao escrever no log '{_log_file_path}': {exc}")
