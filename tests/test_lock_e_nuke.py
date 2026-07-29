"""
O flock compartilhado e a guarda do comando destrutivo.

Os dois cobrem o mesmo modo de falha por caminhos opostos: rodar coisa demais ao
mesmo tempo, e rodar a coisa destrutiva sem querer.
"""

import multiprocessing
import os
import tempfile

import pytest

from src.rfb_cnpj_etl.db.lock import LockOcupado, lock_do_pipeline
from src.rfb_cnpj_etl.db.postgres_builder import PostgresBuilder


def _segurar(caminho, pronto, solte):
    with lock_do_pipeline(caminho, dono="segurador"):
        pronto.set()
        solte.wait(10)


class TestLock:
    def test_o_segundo_processo_e_recusado_e_sabe_quem_esta_com_o_lock(self):
        # A mensagem importa tanto quanto a recusa: um lock que so diz "ocupado"
        # manda a pessoa procurar no `ps`.
        with tempfile.TemporaryDirectory() as d:
            caminho = os.path.join(d, "pipeline.lock")
            pronto = multiprocessing.Event()
            solte = multiprocessing.Event()
            p = multiprocessing.Process(target=_segurar, args=(caminho, pronto, solte))
            p.start()
            pronto.wait(10)

            try:
                with pytest.raises(LockOcupado, match="segurador"):
                    with lock_do_pipeline(caminho, dono="cnpj-pipeline"):
                        pass
            finally:
                solte.set()
                p.join(10)

    def test_o_lock_e_liberado_ao_sair(self):
        with tempfile.TemporaryDirectory() as d:
            caminho = os.path.join(d, "pipeline.lock")

            with lock_do_pipeline(caminho, dono="primeiro"):
                pass

            # Sem a liberacao, a segunda aquisicao falharia — e o ciclo seguinte
            # nao rodaria, com o processo anterior ja morto.
            with lock_do_pipeline(caminho, dono="segundo"):
                pass

    def test_o_lock_e_liberado_mesmo_com_excecao(self):
        with tempfile.TemporaryDirectory() as d:
            caminho = os.path.join(d, "pipeline.lock")

            with pytest.raises(RuntimeError):
                with lock_do_pipeline(caminho, dono="que_falha"):
                    raise RuntimeError("a carga quebrou")

            with lock_do_pipeline(caminho, dono="seguinte"):
                pass


class TestNuke:
    def test_drop_tables_recusa_sem_a_afirmacao_explicita(self):
        # `drop_tables()` era o PRIMEIRO passo do ETL. Em 25/07/2026 ele
        # consumiu 6h43 sobre um banco ja destruido, e nao havia estado
        # intermediario do qual voltar.
        construtor = PostgresBuilder.__new__(PostgresBuilder)

        with pytest.raises(RuntimeError, match="6h43"):
            PostgresBuilder.drop_tables(construtor)

    def test_a_mensagem_aponta_o_caminho_certo(self):
        construtor = PostgresBuilder.__new__(PostgresBuilder)

        with pytest.raises(RuntimeError) as erro:
            PostgresBuilder.drop_tables(construtor)

        # Recusar sem dizer o que fazer transforma a guarda em obstaculo.
        assert "db cycle" in str(erro.value)
        assert "--i-know-what-im-doing" in str(erro.value)
