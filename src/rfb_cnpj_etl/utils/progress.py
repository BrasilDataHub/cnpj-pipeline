# utils/progress.py

"""
Progress bar.
"""

from os.path import basename
from tqdm import tqdm
from ..utils.logger import print_log


def pbar(total: int, desc: str = "INSERINDO DADOS..."):
    return tqdm(
        total=total,
        desc=desc,
        unit="registros",
        dynamic_ncols=False,
        leave=False,
        bar_format="💾 {desc} {percentage:6.2f}% {bar} [{elapsed}]"
    )


def update_progress(rows_inserted: int,
                    filename: str,
                    insertion_queue,
                    queue_size_max: int,
                    total: int,
                    debug: bool = False,
                    shared: dict = None,
                    lock=None,
                    bar=None,
                    accumulated_total: int = None):
    """
    Updates the progress bar or prints a debug log, in a thread-safe way if needed.

    :params:
        rows_inserted: Number of rows inserted in this step
        filename: Name of the file being processed
        insertion_queue: Insertion queue
        queue_size_max: Maximum queue size
        total: Total number of records to be processed
        debug: If True, prints a detailed log instead of using a bar
        shared: Dictionary shared between threads (optional)
        lock: Lock for synchronization (optional)
        bar: Progress bar instance (optional)
    """
    # Always accumulate the inserted total: the run state, the webhook
    # progress and the loader's return value all read it — even in bar mode.
    if shared is not None and lock is not None:
        with lock:
            shared["inserted_total"] += rows_inserted
            current_inserted = shared["inserted_total"]
    elif accumulated_total is not None:
        current_inserted = accumulated_total
    else:
        return  # Does nothing if there is no way to compute the total

    # --- Bar mode (default): animate the tqdm bar and finish ---
    if not debug:
        bar_instance = bar or (shared or {}).get("bar")
        if bar_instance is not None:
            bar_instance.update(rows_inserted)
        return

    # Compute the current percentage
    current_percent = min(100.0, (current_inserted / total) * 100) if total > 0 else 0

    # Determine the last percentage reported to the LOG
    if shared is not None and lock is not None:
        # Multi-thread mode: take it from the shared dictionary
        last_log_percent = shared.get("last_log_percent", 0.0)
    else:
        # Single-thread mode: take it from an attribute of the function itself
        if not hasattr(update_progress, 'last_log_percent'):
            update_progress.last_log_percent = 0.0
        last_log_percent = update_progress.last_log_percent

    # --- CONTROL LOGIC: ONLY PRINT THE LOG IF IT ADVANCES AT LEAST 1% ---
    if (current_percent - last_log_percent) >= 0.5 or (current_percent == 100.0 and last_log_percent <= 100.0):
        # Update the state of the last reported percentage
        if shared is not None and lock is not None:
            with lock:
                shared["last_log_percent"] = current_percent
        else:
            update_progress.last_log_percent = current_percent

        # Print the log
        queue_size = insertion_queue.qsize()
        fname = basename(filename).upper()
        print_log(
            f"REGISTROS: {current_inserted:>12,.0f}".replace(",", ".") +
            f" ({current_percent:6.2f}%)"
            f" | {fname:<23}" +
            f" | FILA: {queue_size:>2} / {queue_size_max:<2}",
            level="debug"
        )