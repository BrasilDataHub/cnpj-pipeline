# cnpj_data/cnpj_downloader.py

"""
Downloads the CNPJ data files published by the Receita Federal.
"""

import os
import random
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import PriorityQueue
from tqdm import tqdm
from typing import Dict, Optional
from .cnpj_public_data import CNPJDataScraper
from ..utils.logger import print_log, get_timestamp
from ..config import (
    DOWNLOAD_DIR, DOWNLOAD_MAX_CONCURRENTS, BROWSER_AGENTS,
    DOWNLOAD_CHUNK_SIZE, DOWNLOAD_CHUNK_TIMEOUT, DOWNLOAD_MAX_RETRIES
)


class CNPJDownloadTask:
    """
    Downloads a single CNPJ data file from the Receita Federal server.

    :params:
        download_url: URL of the file to download.
        file_path: destination path for the file.
        session: persistent HTTP session.
        headers: HTTP headers sent with the request.
        clean: when True, removes previously downloaded files. Otherwise the
               download resumes from the partial file.
    """

    # Column widths for the download progress bar.
    @classmethod
    def set_bar_width(cls, width: int) -> None:
        cls.W_DESC = width  # file name
        cls.W_PERC = 5  # completion percentage
        cls.W_BAR = 15  # progress bar
        cls.W_ETA = 8  # time remaining
        cls.W_SIZE = 6  # downloaded / total size
        cls.W_SPEED = 9  # download speed

    def __init__(
            self,
            download_url: str,
            file_path: str,
            session: requests.Session,
            headers: Dict[str, str],
            *,
            clean: bool = False,
    ):
        self.download_url = download_url
        self.file_path = file_path
        self.session = session
        self.headers = headers
        self.clean = clean
        self.filename = os.path.basename(self.file_path)
        self.chunk_size = DOWNLOAD_CHUNK_SIZE
        self.chunk_timeout = DOWNLOAD_CHUNK_TIMEOUT
        self.max_retries = DOWNLOAD_MAX_RETRIES

    def start_download_task(self, bar_position: int = 1):
        # Temporary path for the partially downloaded file.
        temp_path = self.file_path + ".part"

        # Create the destination directory if needed.
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

        # Always remove the partial file from a previous process.
        if os.path.exists(temp_path):
            os.remove(temp_path)

        # With clean=True, also remove the completed file, if any.
        if self.clean and os.path.exists(self.file_path):
            os.remove(self.file_path)

        for download_attempt in range(1, self.max_retries + 1):

            # Size of the partial file, if any (resume point).
            temp_file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0

            # File already fully downloaded: return the existing path.
            if os.path.exists(self.file_path):
                time.sleep(0.5)
                return self.file_path

            request_headers = self.headers.copy()

            # Partial file present: ask the server to resume from that byte.
            if temp_file_size:
                request_headers["Range"] = f"bytes={temp_file_size}-"

            try:
                resp = self.session.get(
                    self.download_url,
                    headers=request_headers,
                    stream=True,
                    timeout=self.chunk_timeout,
                )
                resp.raise_for_status()

                # Partial response (Content-Range): total comes from the header.
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    total = int(content_range.split("/")[-1])
                else:  # otherwise, add what was already downloaded to the remaining length
                    total = int(resp.headers.get("Content-Length", 0)) + temp_file_size

                # Partial file already complete: promote it to the final path.
                if temp_file_size >= total:
                    os.replace(temp_path, self.file_path)
                    time.sleep(0.5)
                    return self.file_path

                # File write mode: ab = append (resume), wb = write (new).
                mode = "ab" if temp_file_size else "wb"

                # ETA display format for the progress bar.
                tqdm.format_interval = lambda secs: time.strftime('%H:%M:%S', time.gmtime(secs))

                # Progress bar layout.
                desc = f"{self.filename:<{self.W_DESC}}"
                bar_fmt = (
                    f"{{desc}} | "
                    f"{{percentage:{self.W_PERC}.1f}}% "
                    f"{{bar:{self.W_BAR}}} | "
                    f"{{remaining:{self.W_ETA}}} | "
                    f"{{n_fmt:>{self.W_SIZE}}}{{unit}} de "
                    f"{{total_fmt:>{self.W_SIZE}}}{{unit}} | "
                    f"{{rate_fmt:>{self.W_SPEED}}}"
                )

                # Fixed width consumed by each column.
                ncols = (
                        self.W_DESC + 3 +
                        self.W_PERC + 2 +
                        self.W_BAR + 2 +
                        self.W_ETA +
                        self.W_SIZE + len("MB") + 1 + self.W_SIZE + len("MB") +
                        self.W_SPEED + 11
                )

                # Download with a visible progress bar.
                with open(temp_path, mode) as f, tqdm(
                        position=bar_position,
                        leave=False,
                        total=total,
                        initial=temp_file_size,
                        unit="B",
                        unit_scale=True,
                        unit_divisor=1024,
                        desc=desc,
                        bar_format=bar_fmt,
                        ncols=ncols,
                        ascii=False
                ) as pbar:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        f.write(chunk)
                        pbar.update(len(chunk))

                # Fully downloaded: promote the temp file to the final path.
                os.replace(temp_path, self.file_path)

                return self.file_path

            except requests.HTTPError as e:
                # 416 (Range Not Satisfiable): our resume offset is invalid for
                # the server. The partial file is useless — discard it so the
                # next attempt restarts from scratch instead of looping with
                # the same bad Range header.
                if e.response is not None and e.response.status_code == 416:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    continue

                if download_attempt < self.max_retries:
                    continue

            except Exception:
                if download_attempt < self.max_retries:
                    continue

        # All attempts exhausted.
        raise RuntimeError(f"❌ {self.filename.upper()} DOWNLOAD INTERROMPIDO APÓS {self.max_retries} TENTATIVAS")


class CNPJDownloadManager:
    """
    Manages the download queue of CNPJ data files.

    :params:
        month_year: month/year to download. When None, uses the latest.
        download_dir: destination directory. When None, uses DOWNLOAD_DIR.
        concurrents: maximum simultaneous downloads. When None, uses
                     DOWNLOAD_MAX_CONCURRENTS.
        clean: when True, removes previously downloaded files. Otherwise the
               downloads resume.
    """

    def __init__(
            self,
            month_year: Optional[str] = None,
            download_dir: Optional[str] = None,
            concurrents: Optional[int] = None,
            clean: Optional[bool] = False,
    ):
        print_log(f"INICIANDO DOWNLOAD...", level="start")
        self.source = CNPJDataScraper()
        if not month_year:
            month_year = self.source.get_latest()
        if not download_dir:
            download_dir = DOWNLOAD_DIR
        if not concurrents:
            concurrents = DOWNLOAD_MAX_CONCURRENTS

        self.agents = BROWSER_AGENTS
        self.session = requests.Session()
        self.month_year = month_year
        self.download_dir = os.path.abspath(download_dir
                                            or DOWNLOAD_DIR)
        self.concurrents = concurrents
        self.clean = clean
        self.file_paths = []  # destination paths of the files
        self.file_urls = []  # source URLs of the files
        self._collect()  # fetch the metadata for the requested period
        self.max_desc = max(len(os.path.basename(p))  # widest file name (bar layout)
                            for p in self.file_paths)

    def _collect(self):
        """Collects file paths and URLs for the requested period."""
        for rel, info in self.source.get_metadata(self.month_year).items():
            self.file_paths.append(os.path.join(self.download_dir, rel))
            self.file_urls.append(info["file_url"])

    def start_download_queue(self, on_progress=None):
        """Downloads the file queue.

        Raises RuntimeError when any file fails for good: a "successful"
        download step with files missing would only push the failure into the
        validation/load steps (or hide it entirely when validation is
        skipped). The state marks the step as failed and a resume only
        re-downloads what is missing.

        :param on_progress: optional callback invoked per completed file with
            (downloaded_count, total, filename). Used by the run state to
            publish progress to the dashboard.
        """
        CNPJDownloadTask.set_bar_width(self.max_desc)

        queue_size = len(self.file_urls)
        now, elapsed = get_timestamp()
        print_log(f"{queue_size} ARQUIVOS DISPONÍVEIS NO SITE DA RECEITA FEDERAL ({self.month_year})", level="web")
        desc = (f"🕑 {now} "
                f"|⏱️ {elapsed} "
                f"|📋 {queue_size} ARQUIVOS RESTANTES. BAIXANDO")
        remaining_bar = tqdm(
            position=0,
            leave=False,
            total=queue_size,
            desc=desc,
            bar_format="{desc} "
        )

        # Queue that hands out progress-bar positions to worker threads.
        available_queue_positions = PriorityQueue()
        for pos in range(1, self.concurrents + 1):
            available_queue_positions.put(pos)

        def create_download_task(download_task: CNPJDownloadTask):
            queue_position = available_queue_positions.get()
            try:
                return download_task.start_download_task(bar_position=queue_position)
            finally:
                available_queue_positions.put(queue_position)

        future_task: Dict = {}
        failed_files = []

        with ThreadPoolExecutor(max_workers=self.concurrents) as executor:
            for url, file_path in zip(self.file_urls, self.file_paths):
                headers = {"User-Agent": random.choice(self.agents)}
                task = CNPJDownloadTask(url, file_path, self.session, headers, clean=self.clean)
                future_task[executor.submit(create_download_task, task)] = task

            for future in as_completed(future_task):
                task = future_task[future]
                try:
                    future.result()
                    remaining_bar.update(1)
                    now, elapsed = get_timestamp()
                    remaining = remaining_bar.total - remaining_bar.n

                    remaining_msg = "ARQUIVOS RESTANTES" if remaining > 1 else "ARQUIVO RESTANTE"
                    desc = (f"🕑 {now} "
                            f"|⏱️ {elapsed} "
                            f"|ℹ️ {remaining} {remaining_msg}. BAIXANDO")
                    remaining_bar.set_description(f"{desc}")

                    remaining_bar.refresh()

                    if on_progress is not None:
                        try:
                            on_progress(remaining_bar.n, remaining_bar.total, task.filename)
                        except Exception:
                            pass   # instrumentation must never interrupt the download

                except Exception as e:
                    failed_files.append(task.filename)
                    print_log(f"ERRO AO BAIXAR {task.filename}: {e}", level="error")

        remaining_bar.clear()
        remaining_bar.close()

        if failed_files:
            raise RuntimeError(
                f"DOWNLOAD INCOMPLETO: {len(failed_files)} ARQUIVO(S) FALHARAM: "
                f"{', '.join(sorted(failed_files))}. "
                f"Reexecute o comando para retomar apenas o que falta."
            )

        print_log("DOWNLOAD CONCLUÍDO", level="done")
