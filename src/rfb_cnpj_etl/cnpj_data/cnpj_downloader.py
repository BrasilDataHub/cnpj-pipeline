# cnpj_data/cnpj_downloader.py

"""
Módulo para baixar os arquivos de dados de CNPJ disponíveis no site da Receita Federal.
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
    Classe para baixar um arquivo de dados de CNPJ disponível no site da Receita Federal.

    :params:
        download_url: URL do arquivo a ser baixado.
        download_dir: diretório onde o arquivo será salvo.
        session: sessão HTTP persistente.
        header: cabeçalho HTTP a ser enviado na requisição.
        clean: Se True, remove arquivos baixados anteriormente. Se None, continua o download.
    """

    # define os tamanhos das colunas da informação de download
    @classmethod
    def set_bar_width(cls, width: int) -> None:
        cls.W_DESC = width  # nome do arquivo
        cls.W_PERC = 5  # percentual executado
        cls.W_BAR = 15  # barra de progresso
        cls.W_ETA = 8  # tempo restante
        cls.W_SIZE = 6  # baixado / tamanho total
        cls.W_SPEED = 9  # velocidade de download

    def __init__(
            self,
            download_url: str,
            file_path: str,
            session: requests.Session,
            headers: Dict[str, str],
            *,
            clean: bool = False,
    ):
        self.download_url = download_url  # url do arquivo a ser baixado
        self.file_path = file_path  # caminho do arquivo a ser salvo
        self.session = session  # sessão HTTP persistente
        self.headers = headers  # cabeçalho HTTP a ser enviado na requisição
        self.clean = clean  # se True, remove arquivos baixados anteriormente
        self.filename = os.path.basename(self.file_path)  # nome do arquivo
        self.chunk_size = DOWNLOAD_CHUNK_SIZE  # tamanho do chunk para download
        self.chunk_timeout = DOWNLOAD_CHUNK_TIMEOUT  # tempo limite para download de um chunk
        self.max_retries = DOWNLOAD_MAX_RETRIES  # número máximo de tentativas de download

    def start_download_task(self, bar_position: int = 1):
        # caminho temporário para salvar o arquivo parcialmente baixado
        temp_path = self.file_path + ".part"

        # cria o diretório do arquivo, se não existir
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

        # sempre remove o arquivo parcial
        if os.path.exists(temp_path):
            os.remove(temp_path)

        # se clean = True, remove arquivo parcialmente baixado e o arquivo completo, se existir
        if self.clean and os.path.exists(self.file_path):
            os.remove(self.file_path)

        # tenta baixar o arquivo
        for download_attempt in range(1, self.max_retries + 1):

            # tamanho do arquivo parcialmente baixado, se houver
            temp_file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0

            # se o arquivo já existe, retorna o caminho existente
            if os.path.exists(self.file_path):
                time.sleep(0.5)
                return self.file_path

            # define o cabeçalho HTTP para o download
            request_headers = self.headers.copy()

            # se o arquivo foi parcialmente baixado, define o cabeçalho HTTP para retomar o download
            if temp_file_size:
                request_headers["Range"] = f"bytes={temp_file_size}-"

            try:
                resp = self.session.get(
                    self.download_url,
                    headers=request_headers,
                    stream=True,
                    timeout=self.chunk_timeout,
                )
                resp.raise_for_status()  # verifica se a requisição foi bem-sucedida

                # se a resposta da requisição for parcial (content-range), usa o valor do cabeçalho
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    total = int(content_range.split("/")[-1])
                else:  # senão, soma o que já foi baixado ao tamanho do arquivo parcialmente baixado
                    total = int(resp.headers.get("Content-Length", 0)) + temp_file_size

                # se o arquivo já foi baixado por completo, retorna o caminho do arquivo
                if temp_file_size >= total:
                    os.replace(temp_path, self.file_path)
                    time.sleep(0.5)
                    return self.file_path

                # modo de escrita do arquivo
                # ab = append (continuar), wb = write (novo)
                mode = "ab" if temp_file_size else "wb"

                # define o formato de exibição do tempo estimado na barra de progresso
                tqdm.format_interval = lambda secs: time.strftime('%H:%M:%S', time.gmtime(secs))

                # formatação da barra de progresso
                desc = f"{self.filename:<{self.W_DESC}}"
                bar_fmt = (
                    f"{{desc}} | "  # primeira coluna com o nome do arquivo
                    f"{{percentage:{self.W_PERC}.1f}}% "  # segunda coluna com o percentual executado
                    f"{{bar:{self.W_BAR}}} | "  # terceira coluna com a barra de progresso
                    f"{{remaining:{self.W_ETA}}} | "  # quarta coluna com o tempo estimado
                    f"{{n_fmt:>{self.W_SIZE}}}{{unit}} de "  # quinta coluna com o tamanho do arquivo
                    f"{{total_fmt:>{self.W_SIZE}}}{{unit}} | "  # sexta coluna com a velocidade de download 
                    f"{{rate_fmt:>{self.W_SPEED}}}"  # sétima coluna com a velocidade de download
                )

                # espaços ocupados por cada coluna (fixo)
                ncols = (
                        self.W_DESC + 3 +
                        self.W_PERC + 2 +
                        self.W_BAR + 2 +
                        self.W_ETA +
                        self.W_SIZE + len("MB") + 1 + self.W_SIZE + len("MB") +
                        self.W_SPEED + 11
                )

                # executa o download com barra de progresso visível
                with open(temp_path, mode) as f, tqdm(
                        position=bar_position,  # posição da barra de progresso na lista
                        leave=False,  # não deixa a barra de progresso visível após o término
                        total=total,  # tamanho total do arquivo
                        initial=temp_file_size,  # tamanho do arquivo parcialmente baixado
                        unit="B",  # unidade de medida do tamanho do arquivo
                        unit_scale=True,  # escala automática da unidade de medida
                        unit_divisor=1024,  # divisor da unidade de medida
                        desc=desc,  # descrição (nome do arquivo)
                        bar_format=bar_fmt,  # formato da barra de progresso
                        ncols=ncols,  # tamanho da barra de progresso
                        ascii=False  # habilita caracteres unicode
                ) as pbar:
                    # itera sobre os chunks do arquivo
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        f.write(chunk)  # escreve o chunk no arquivo
                        pbar.update(len(chunk))  # atualiza a barra de progresso

                # se o arquivo foi baixado por completo, renomeia o arquivo temporário para o arquivo final
                os.replace(temp_path, self.file_path)

                return self.file_path

            except requests.HTTPError as e:
                # se a resposta da requisição for 416 (Range Not Satisfiable)
                if e.response.status_code == 416:
                    if os.path.exists(self.file_path):
                        os.remove(temp_path)  # remove o arquivo temporário
                    continue  # tenta novamente

                # se ainda há tentativas
                if download_attempt < self.max_retries:
                    continue  # tenta novamente

            except Exception:
                # se ainda há tentativas
                if download_attempt < self.max_retries:
                    continue  # tenta novamente

        # lança um erro após esgotar o número máximo de tentativas
        raise RuntimeError(f"❌ {self.filename.upper()} DOWNLOAD INTERROMPIDO APÓS {self.max_retries} TENTATIVAS")


class CNPJDownloadManager:
    """
    Classe para gerenciar o download de arquivos de dados de CNPJ disponíveis no site da Receita Federal.

    :params:
        month_year: Mês/ano para baixar. Se None, baixa o mais recente.
        download_dir: Diretório para salvar os arquivos baixados. Se None, usa DOWNLOAD_DIR.
        concurrents: Número máximo de downloads concorrentes. Se None, usa DOWNLOAD_MAX_CONCURRENTS.
        clean: Se True, remove arquivos baixados anteriormente. Se None, continua o download.
    """

    def __init__(
            self,
            month_year: Optional[str] = None,
            download_dir: Optional[str] = None,
            concurrents: Optional[int] = None,
            clean: Optional[bool] = False,
    ):
        print_log(f"INICIANDO DOWNLOAD...", level="start")
        self.source = CNPJDataScraper()  # objeto para obter dados do período
        if not month_year:
            month_year = self.source.get_latest()  # se não fornecido, obtém o mês mais recente
        if not download_dir:
            download_dir = DOWNLOAD_DIR  # se não fornecido, usa DOWNLOAD_DIR
        if not concurrents:
            concurrents = DOWNLOAD_MAX_CONCURRENTS  # se não fornecido, usa DOWNLOAD_MAX_CONCURRENTS


        self.agents = BROWSER_AGENTS  # lista de agentes de navegador
        self.session = requests.Session()  # cria uma sessão HTTP persistente
        self.month_year = month_year  # mês/ano para baixar
        self.download_dir = os.path.abspath(download_dir  # diretório para salvar os arquivos
                                            or DOWNLOAD_DIR)
        self.concurrents = concurrents  # número máximo de downloads concorrentes
        self.clean = clean  # se True, remove arquivos baixados anteriormente
        self.file_paths = []  # lista de caminhos para os arquivos baixados
        self.file_urls = []  # lista de URLs para os arquivos
        self._collect()  # coleta os dados do período informado
        self.max_desc = max(len(os.path.basename(p))  # largura máxima dentre os nomes dos arquivos
                            for p in self.file_paths)

    # coletar os dados do períoro informado
    def _collect(self):
        for rel, info in self.source.get_metadata(self.month_year).items():  # itera sobre os dados do período
            self.file_paths.append(os.path.join(self.download_dir, rel))  # adiciona o caminho do arquivo
            self.file_urls.append(info["file_url"])  # adiciona o URL do arquivo

    # iniciar os downloads
    def start_download_queue(self, on_progress=None):
        """Baixa a fila de arquivos.

        :param on_progress: callback opcional chamado a cada arquivo concluído,
            com (baixados, total, nome_do_arquivo). Usado pelo estado de
            execução para publicar progresso no dashboard.
        """
        CNPJDownloadTask.set_bar_width(self.max_desc)  # define a largura da barra de progresso

        queue_size = len(self.file_urls)  # total de downloads a serem realizados
        now, elapsed = get_timestamp()  # obtém a hora atual e o tempo decorrido
        print_log(f"{queue_size} ARQUIVOS DISPONÍVEIS NO SITE DA RECEITA FEDERAL ({self.month_year})", level="web")
        desc = (f"🕑 {now} "
                f"|⏱️ {elapsed} "
                f"|📋 {queue_size} ARQUIVOS RESTANTES. BAIXANDO")
        remaining_bar = tqdm(
            position=0,  # posição da barra de progresso na lista
            leave=False,  # não deixa a barra de progresso visível após o término
            total=queue_size,  # tamanho total do arquivo
            desc=desc,
            bar_format="{desc} "
        )

        # fila para controlar a posição das barras de progresso
        available_queue_positions = PriorityQueue()
        for pos in range(1, self.concurrents + 1):
            available_queue_positions.put(pos)

        def create_download_task(download_task: CNPJDownloadTask):
            queue_position = available_queue_positions.get()
            try:
                return download_task.start_download_task(bar_position=queue_position)
            finally:
                available_queue_positions.put(queue_position)

        future_task: Dict = {}  # dicionário para armazenar as tarefas em execução

        # cria um executor de thread
        with ThreadPoolExecutor(max_workers=self.concurrents) as executor:
            # itera sobre as URLs e caminhos dos arquivos
            for url, file_path in zip(self.file_urls, self.file_paths):
                # seleciona um agente aleatório
                headers = {"User-Agent": random.choice(self.agents)}
                # cria uma tarefa de download
                task = CNPJDownloadTask(url, file_path, self.session, headers, clean=self.clean)
                # adiciona a tarefa ao dicionário
                future_task[executor.submit(create_download_task, task)] = task

            # aguarda todas as tarefas serem concluídas
            for future in as_completed(future_task):
                task = future_task[future]  # obtém a tarefa associada ao future
                try:
                    future.result()  # aguarda o término da tarefa
                    remaining_bar.update(1)  # avança a barra de progresso
                    now, elapsed = get_timestamp()  # obtém hora atual e tempo decorrido
                    remaining = remaining_bar.total - remaining_bar.n  # arquivos restantes após update

                    # atualiza a descrição da barra de progresso principal
                    remaining_msg = "ARQUIVOS RESTANTES" if remaining > 1 else "ARQUIVO RESTANTE"
                    desc = (f"🕑 {now} "
                            f"|⏱️ {elapsed} "
                            f"|ℹ️ {remaining} {remaining_msg}. BAIXANDO")
                    remaining_bar.set_description(f"{desc}")

                    remaining_bar.refresh()  # força atualização visual da barra

                    if on_progress is not None:
                        try:
                            on_progress(remaining_bar.n, remaining_bar.total, task.filename)
                        except Exception:
                            pass   # instrumentação nunca interrompe o download

                except Exception as e:
                    print_log(f"ERRO AO BAIXAR {task.filename}: {e}", level="error")

        remaining_bar.clear()
        remaining_bar.close()

        print_log("DOWNLOAD CONCLUÍDO", level="done")
