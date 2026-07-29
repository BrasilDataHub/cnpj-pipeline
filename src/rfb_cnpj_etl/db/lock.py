# db/lock.py

"""
O ``flock`` compartilhado entre os tres servicos do ciclo mensal.

O PORQUE
========

``cnpj-pipeline``, ``sitemap-service`` e ``search-indexer`` disputam a MESMA
NVMe e o MESMO banco. Rodar dois deles ao mesmo tempo nao produz erro: produz
lentidao, arquivos temporarios, checkpoints forcados e uma janela de carga que
estoura sem que ninguem saiba por que.

Hoje a serializacao depende de disciplina humana — alguem lembrar de nao
disparar o indexer enquanto o ETL roda. Sao ~20 linhas para torna-la mecanica.

O lock e ADVISORY e por ARQUIVO, nao no banco: um lock no Postgres cairia junto
com a conexao, e a conexao cai o tempo todo numa carga de horas. ``flock`` no
filesystem sobrevive a qualquer coisa menos ao fim do processo — que e
exatamente o momento em que ele DEVE ser solto.
"""

import errno
import fcntl
import os
import time
from contextlib import contextmanager
from typing import Optional

from ..utils.logger import print_log

#: Caminho compartilhado pelos tres servicos. Fora de /tmp de proposito: /tmp e
#: limpo no boot em varias distribuicoes, e um lock que some no reboot nao
#: protege a carga que comecou antes dele.
CAMINHO_PADRAO = "/var/lib/bdh/pipeline.lock"


class LockOcupado(RuntimeError):
    """Outro serviço do ciclo já está com o lock."""


@contextmanager
def lock_do_pipeline(
    caminho: Optional[str] = None,
    *,
    dono: str = "cnpj-pipeline",
    espera_segundos: int = 0,
):
    """
    Adquire o lock do ciclo mensal, ou falha dizendo QUEM está com ele.

    ``espera_segundos=0`` é o default e é deliberado: se o indexer está
    rodando, esperar horas em silêncio é pior que falhar agora com a mensagem
    certa. Quem quiser enfileirar passa um tempo de espera explícito.

    O PID e o dono são escritos no arquivo para que a mensagem de erro diga o
    que está acontecendo — um lock que só diz "ocupado" manda a pessoa procurar
    no `ps`.
    """
    caminho = caminho or os.getenv("PIPELINE_LOCK_FILE", CAMINHO_PADRAO)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)

    arquivo = open(caminho, "a+")
    limite = time.monotonic() + espera_segundos

    while True:
        try:
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EAGAIN):
                arquivo.close()
                raise

            if time.monotonic() >= limite:
                arquivo.seek(0)
                ocupante = arquivo.read().strip() or "processo desconhecido"
                arquivo.close()
                raise LockOcupado(
                    f"o ciclo mensal ja esta em execucao: {ocupante}. "
                    f"cnpj-pipeline, sitemap-service e search-indexer compartilham "
                    f"{caminho} de proposito — rodar dois ao mesmo tempo nao da erro, "
                    f"da uma janela de carga que estoura sem explicacao."
                )

            time.sleep(1)

    try:
        arquivo.seek(0)
        arquivo.truncate()
        arquivo.write(f"{dono} pid={os.getpid()} desde={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        arquivo.flush()
        print_log(f"lock do ciclo adquirido por {dono} (pid {os.getpid()})", "SUCCESS")
        yield arquivo
    finally:
        try:
            arquivo.seek(0)
            arquivo.truncate()
            arquivo.flush()
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_UN)
        finally:
            arquivo.close()
            print_log(f"lock do ciclo liberado por {dono}", "INFO")
