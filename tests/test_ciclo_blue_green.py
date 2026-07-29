"""
O ciclo blue/green contra um Postgres REAL, incluindo o ENSAIO do item 26.

Por que contra um banco de verdade e nao com mocks: o que esta sob teste sao
`search_path`, `FULL OUTER JOIN` entre schemas, `ALTER DATABASE` e privilegios.
Um mock confirmaria que as strings de SQL foram montadas; nao confirmaria que o
Postgres faz o que se espera delas — e a trava 4 do roadmap exige exatamente
isso: "publish abortado DE PROPOSITO e o rollback exercitado".

O teste sobe um Postgres em container, com dados minusculos. Ele valida a
MECANICA; o wall-clock de producao e outra medicao.
"""

import os
import subprocess
import time
import uuid

import psycopg2
import pytest

from src.rfb_cnpj_etl.db import cycle
from src.rfb_cnpj_etl.db import schema_target as st

CONTAINER = "bdhtest-ciclo-pg"
PORTA = 15499
SENHA = "ensaio"


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


@pytest.fixture(scope="module")
def conn():
    if _docker("info").returncode != 0:
        pytest.skip("docker indisponivel")

    _docker("rm", "-f", CONTAINER)
    _docker(
        "run", "-d", "--name", CONTAINER,
        "-e", f"POSTGRES_PASSWORD={SENHA}", "-e", "POSTGRES_DB=ensaio",
        "-p", f"127.0.0.1:{PORTA}:5432", "postgres:17.10",
    )

    conexao = None
    for _ in range(40):
        try:
            conexao = psycopg2.connect(
                host="127.0.0.1", port=PORTA, dbname="ensaio",
                user="postgres", password=SENHA,
            )
            break
        except psycopg2.OperationalError:
            time.sleep(2)

    if conexao is None:
        _docker("rm", "-f", CONTAINER)
        pytest.skip("o Postgres de teste nao subiu")

    yield conexao

    conexao.close()
    _docker("rm", "-f", CONTAINER)


@pytest.fixture(autouse=True)
def _transacao_limpa(conn):
    """
    Uma excecao esperada deixa a transacao ABORTADA, e todo comando seguinte
    falha com `current transaction is aborted` — um erro que nao tem nada a ver
    com o que se esta testando. Metade dos testes deste arquivo espera excecao
    de proposito.
    """
    conn.rollback()
    yield
    conn.rollback()


def _carregar_schema(conn, load_id, linhas, alterar=None):
    """Cria um schema de dados minusculo, com row_hash — a coluna do gate."""
    schema = st.nome_do_schema(load_id)
    alterar = alterar or set()

    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
        cur.execute(f'DROP TABLE IF EXISTS "{schema}".busca_estabelecimento;')
        cur.execute(
            f'CREATE TABLE "{schema}".busca_estabelecimento '
            f"(cnpj_completo TEXT PRIMARY KEY, row_hash UUID);"
        )
        # CASCADE: a MV de teste depende desta tabela, e um DROP sem cascade
        # falha com "dependent objects still exist" na segunda montagem do
        # cenario.
        cur.execute(f'DROP TABLE IF EXISTS "{schema}".estabelecimento CASCADE;')
        cur.execute(f'CREATE TABLE "{schema}".estabelecimento (cnpj_completo TEXT PRIMARY KEY);')

        for i in range(linhas):
            cnpj = f"{i:014d}"
            marca = "alterado" if i in alterar else "original"
            cur.execute(
                f'INSERT INTO "{schema}".busca_estabelecimento VALUES (%s, md5(%s)::uuid);',
                (cnpj, cnpj + marca),
            )
            cur.execute(f'INSERT INTO "{schema}".estabelecimento VALUES (%s);', (cnpj,))

        # Uma MV nao vazia: o portao de qualidade recusa MV ausente E MV vazia.
        cur.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{schema}".mv_teste;')
        cur.execute(
            f'CREATE MATERIALIZED VIEW "{schema}".mv_teste AS '
            f'SELECT count(*) AS total FROM "{schema}".estabelecimento;'
        )

    conn.commit()
    return schema


class TestCicloCompleto:
    def test_bootstrap_cria_infraestrutura_e_e_idempotente(self, conn):
        cycle.bootstrap(conn, "202601", "ensaio")
        # Reexecutar do zero e no-op: e o que separa "retomar" de "recomecar",
        # e a diferenca entre perder minutos e perder as 6h43 de 25/07.
        cycle.bootstrap(conn, "202601", "ensaio")

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname IN ('ext','meta','dados_202601');")
            assert cur.fetchone()[0] == 3

    def test_publish_troca_o_search_path_padrao(self, conn):
        _carregar_schema(conn, "202601", 100)
        cycle.publish(conn, "202601", "ensaio", pular_validacao=True)

        assert st.schema_vigente(conn, "ensaio") == "dados_202601"

    def test_gate_aprova_delta_pequeno(self, conn):
        # Duas linhas mudadas em 100 = 2%, bem abaixo do teto de 25%.
        _carregar_schema(conn, "202602", 100, alterar={7, 42})

        resultado = cycle.gate_de_delta(conn, "dados_202602", "dados_202601")

        assert resultado.aprovado
        assert resultado.linhas_alteradas == 2
        assert resultado.delta_pct == pytest.approx(2.0, abs=0.01)

    def test_gate_ABORTA_com_arquivo_truncado(self, conn):
        # 40 linhas contra 100 = 60% de delta. E a assinatura de arquivo-fonte
        # truncado: carrega sem erro nenhum e parece uma carga completa.
        _carregar_schema(conn, "202603", 40)

        resultado = cycle.gate_de_delta(conn, "dados_202603", "dados_202601")

        assert not resultado.aprovado
        assert resultado.linhas_removidas == 60

    def test_validate_recusa_carga_truncada(self, conn):
        with pytest.raises(cycle.CicloAbortado, match="gate de delta"):
            cycle.validate(
                conn, "dados_202603", "dados_202601", minimo_estabelecimentos=1
            )

    def test_validate_recusa_mv_vazia(self, conn):
        schema = _carregar_schema(conn, "202604", 100)
        with conn.cursor() as cur:
            # MV vazia passa por qualquer CONTAGEM de MVs e quebra a pagina que
            # a consome — o sintoma aparece como "o ranking sumiu".
            cur.execute(f'DROP MATERIALIZED VIEW "{schema}".mv_teste;')
            cur.execute(
                f'CREATE MATERIALIZED VIEW "{schema}".mv_teste AS '
                f'SELECT 1 AS x WHERE false;'
            )
        conn.commit()

        with pytest.raises(cycle.CicloAbortado, match="VAZIA"):
            cycle.validate(conn, schema, "dados_202601", minimo_estabelecimentos=1)


class TestEnsaio:
    """
    O ENSAIO do item 26, que e a trava 4 do prompt de execucao.

    Publish abortado DE PROPOSITO, e o rollback exercitado. Sem isto, o ciclo
    real usa um caminho que nunca falhou de mentira — e a primeira falha de
    verdade acontece com dados de verdade.
    """

    def test_publish_abortado_nao_move_o_search_path(self, conn):
        # Cada classe monta o proprio cenario: depender da ordem de execucao de
        # outra classe faz o teste passar em conjunto e falhar isolado, que e a
        # pior forma de teste verde.
        _carregar_schema(conn, "202601", 100)
        cycle.publish(conn, "202601", "ensaio", pular_validacao=True)
        antes = st.schema_vigente(conn, "ensaio")

        # A carga de 202603 esta truncada (40 de 100). O publish TEM de abortar.
        _carregar_schema(conn, "202603", 40)

        with pytest.raises(cycle.CicloAbortado):
            cycle.publish(conn, "202603", "ensaio")

        depois = st.schema_vigente(conn, "ensaio")

        # O ponto do ensaio: o site continuou servindo o schema bom durante todo
        # o exercicio. Nao houve janela em que ele apontasse para a carga ruim.
        assert depois == antes == "dados_202601"

    def test_rollback_volta_para_a_geracao_anterior(self, conn):
        cycle.criar_tabela_de_ciclos(conn)
        _carregar_schema(conn, "202601", 100)
        _carregar_schema(conn, "202602", 100, alterar={1})

        cycle.publish(conn, "202601", "ensaio", pular_validacao=True)
        cycle.registrar_etapa(conn, "202601", "publicado", publicado_em="now()")

        cycle.publish(conn, "202602", "ensaio", pular_validacao=True)
        cycle.registrar_etapa(conn, "202602", "publicado", publicado_em="now()")

        assert st.schema_vigente(conn, "ensaio") == "dados_202602"

        inicio = time.monotonic()
        destino = cycle.rollback(conn, "ensaio")
        duracao = time.monotonic() - inicio

        assert destino == "dados_202601"
        assert st.schema_vigente(conn, "ensaio") == "dados_202601"
        # O criterio de aceite e "< 60 s". A operacao e um ALTER DATABASE: ela
        # nao depende do TAMANHO dos dados, e e por isso que o numero vale.
        assert duracao < 60

    def test_o_gc_nunca_inclui_o_n1(self, conn):
        # O N-1 e o rollback. Apaga-lo no mesmo ciclo elimina a unica volta que
        # nao custa 40 minutos de restore.
        cycle.criar_tabela_de_ciclos(conn)
        _carregar_schema(conn, "202601", 100)
        _carregar_schema(conn, "202602", 100)

        cycle.publish(conn, "202601", "ensaio", pular_validacao=True)
        cycle.registrar_etapa(conn, "202601", "publicado", publicado_em="now()")
        cycle.publish(conn, "202602", "ensaio", pular_validacao=True)
        cycle.registrar_etapa(conn, "202602", "publicado", publicado_em="now()")

        expurgo = cycle.schemas_para_expurgo(conn, "ensaio")

        vigente = st.schema_vigente(conn, "ensaio")
        assert vigente == "dados_202602"
        assert vigente not in expurgo
        assert "dados_202601" not in expurgo  # o N-1 e o rollback


class TestSalvaguardaEmBanco:
    def test_o_revoke_impede_escrita_no_schema_vigente(self, conn):
        """
        A salvaguarda funcionando de verdade, e nao só montando o SQL certo.

        Com um role NAO superusuario, escrever no schema vigente tem de falhar
        com `permission denied` — que e o que transforma um `public.` literal
        esquecido de "corrompeu a base" em "a carga falhou".
        """
        with conn.cursor() as cur:
            cur.execute("DROP ROLE IF EXISTS etl_ensaio;")
            cur.execute("CREATE ROLE etl_ensaio LOGIN PASSWORD 'x';")
            cur.execute('GRANT USAGE ON SCHEMA "dados_202601" TO etl_ensaio;')
            cur.execute('GRANT CREATE ON SCHEMA "dados_202601" TO etl_ensaio;')
            cur.execute(
                'GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "dados_202601" TO etl_ensaio;'
            )
        conn.commit()

        st.revogar_escrita_no_schema_vigente(conn, "dados_202601", role="etl_ensaio")

        como_etl = psycopg2.connect(
            host="127.0.0.1", port=PORTA, dbname="ensaio",
            user="etl_ensaio", password="x",
        )
        try:
            with como_etl.cursor() as cur:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(
                        'INSERT INTO "dados_202601".estabelecimento VALUES (%s);',
                        (str(uuid.uuid4())[:14],),
                    )
            como_etl.rollback()

            with como_etl.cursor() as cur:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute('CREATE TABLE "dados_202601".intrusa (x int);')
        finally:
            como_etl.close()
