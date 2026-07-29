"""
A salvaguarda do blue/green por schema.

Este e o unico ponto do plano onde errar custa caro: um ``public.`` literal
esquecido na parametrizacao escreveria no schema que esta SERVINDO — nao no que
esta sendo construido. O sintoma seria o site exibindo dados corrompidos durante
uma carga que ninguem sabe que falhou.

O que esta sob teste nao e "o REVOKE roda". E que ele:

  * roda ANTES do ciclo (aplicado no fim, protegeria a proxima execucao);
  * AVISA em vez de fingir, quando o role e superusuario e o REVOKE nao tem
    efeito nenhum;
  * deixa ``public`` FORA do search_path, para que o literal nao resolva.
"""

from unittest.mock import MagicMock

import pytest

from src.rfb_cnpj_etl.db import schema_target as st


class CursorFalso:
    def __init__(self, respostas=None):
        self.executados = []
        self._respostas = respostas or {}
        self._ultimo = None

    def execute(self, sql, params=None):
        self.executados.append(sql)
        self._ultimo = sql

    def fetchone(self):
        for chave, valor in self._respostas.items():
            if self._ultimo and chave in self._ultimo:
                return valor
        return None

    def fetchall(self):
        for chave, valor in self._respostas.items():
            if self._ultimo and chave in self._ultimo:
                return valor
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn(respostas=None):
    conn = MagicMock()
    cursor = CursorFalso(respostas)
    conn.cursor.return_value = cursor
    conn._cursor = cursor
    return conn


class TestRevoke:
    def test_retira_as_permissoes_de_escrita_do_schema_vigente(self):
        conn = _conn({"current_user": ("etl",), "rolsuper": (False,)})

        st.revogar_escrita_no_schema_vigente(conn, "dados_202607")

        sql = " ".join(conn._cursor.executados)
        assert 'REVOKE CREATE ON SCHEMA "dados_202607"' in sql
        assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES" in sql
        # E o padrao para tabelas FUTURAS: sem ele, uma tabela criada por outro
        # caminho no schema vigente nasceria gravavel.
        assert "ALTER DEFAULT PRIVILEGES" in sql

    def test_avisa_em_vez_de_fingir_quando_o_role_e_superusuario(self, capsys):
        # GRANT/REVOKE nao tem efeito sobre superusuario. Sem o aviso, a
        # salvaguarda PARECERIA aplicada e nao estaria — a pior combinacao
        # possivel, porque a equipe confiaria nela.
        conn = _conn({"current_user": ("postgres",), "rolsuper": (True,)})

        st.revogar_escrita_no_schema_vigente(conn, "dados_202607")

        saida = capsys.readouterr().out
        assert "SUPERUSU" in saida.upper()
        assert "REVOKE" not in " ".join(conn._cursor.executados)

    def test_primeira_carga_nao_tem_o_que_revogar(self):
        conn = _conn()

        st.revogar_escrita_no_schema_vigente(conn, "")

        assert conn._cursor.executados == []


class TestSearchPath:
    def test_public_fica_fora_do_search_path(self):
        # Com `public` no caminho, um `public.` esquecido continuaria
        # resolvendo — e a salvaguarda perderia metade da forca.
        conn = _conn()

        st.definir_search_path(conn, "dados_202608")

        sql = " ".join(conn._cursor.executados)
        assert '"dados_202608"' in sql
        assert "public" not in sql

    def test_ext_entra_no_search_path(self):
        # As extensoes moram em `ext`. Sem ele no caminho, `unaccent(...)` e
        # `gin_trgm_ops` deixam de resolver e todo DDL de indice textual falha.
        conn = _conn()

        st.definir_search_path(conn, "dados_202608")

        assert '"ext"' in " ".join(conn._cursor.executados)


class TestExtensoes:
    def test_extensoes_sao_criadas_fora_do_public(self):
        # `pg_trgm`, `unaccent` e `pg_stat_statements` estao DENTRO do public
        # hoje. Num desenho em que o public possa ser renomeado, elas iriam
        # junto — levando todo indice GIN que depende de gin_trgm_ops.
        conn = _conn()

        st.criar_schemas_de_infraestrutura(conn)

        sql = " ".join(conn._cursor.executados)
        for extensao in ("pg_trgm", "unaccent", "pg_stat_statements"):
            assert f'CREATE EXTENSION IF NOT EXISTS "{extensao}" SCHEMA "ext"' in sql

    def test_meta_e_criado_junto(self):
        conn = _conn()

        st.criar_schemas_de_infraestrutura(conn)

        assert 'CREATE SCHEMA IF NOT EXISTS "meta"' in " ".join(conn._cursor.executados)


class TestPublicacao:
    def test_publicar_e_uma_troca_de_search_path_padrao(self):
        # A operacao INTEIRA do blue/green. E por isso que a indisponibilidade
        # cai de 3h30-6h para menos de 5 segundos.
        conn = _conn()

        st.publicar_schema(conn, "dados_202608", "dados_cnpj")

        sql = " ".join(conn._cursor.executados)
        assert 'ALTER DATABASE "dados_cnpj" SET search_path' in sql
        assert '"dados_202608"' in sql

    def test_o_publish_avisa_sobre_conexoes_abertas(self, capsys):
        # Conexoes JA ABERTAS continuam lendo o schema anterior ate
        # reconectarem, e servem dados velhos SEM ERRO. E por isso que o
        # RECONNECT do PgBouncer faz parte do publish.
        conn = _conn()

        st.publicar_schema(conn, "dados_202608", "dados_cnpj")

        assert "RECONNECT" in capsys.readouterr().out

    def test_nome_do_schema_segue_o_load_id(self):
        assert st.nome_do_schema("202608") == "dados_202608"


class TestSchemaVigente:
    def test_le_o_schema_publicado_do_catalogo(self):
        conn = _conn({"pg_db_role_setting": [("search_path=dados_202607, ext",)]})

        assert st.schema_vigente(conn, "dados_cnpj") == "dados_202607"

    def test_ignora_configuracao_que_nao_e_de_schema_de_dados(self):
        conn = _conn({"pg_db_role_setting": [("statement_timeout=5000",)]})

        assert st.schema_vigente(conn, "dados_cnpj") is None


class TestParametrizacao:
    """
    ``qualificar()`` e a unica forma de nomear uma tabela.

    ESTE E O UNICO PONTO DO PLANO ONDE ERRAR CUSTA CARO. Um ``public."{tabela}"``
    literal sobrevivente escreveria no schema que esta SERVINDO enquanto o outro
    e construido — e o sintoma seria o site exibindo dados corrompidos durante
    uma carga que ninguem sabe que falhou.
    """

    ARQUIVOS = [
        "src/rfb_cnpj_etl/db/postgres_builder.py",
        "src/rfb_cnpj_etl/db/search_table.py",
        "src/rfb_cnpj_etl/db/advanced_indexes.py",
        "src/rfb_cnpj_etl/db/postgres_loader.py",
    ]

    @pytest.mark.parametrize("arquivo", ARQUIVOS)
    def test_nenhum_public_literal_sobrou(self, arquivo):
        from pathlib import Path

        caminho = Path(arquivo)

        if not caminho.exists():
            pytest.skip(f"{arquivo} nao existe")

        conteudo = caminho.read_text()

        assert 'public."{' not in conteudo, (
            f"{arquivo} tem um `public.\"{{...}}\"` literal. Ele escreveria no schema "
            "que esta servindo, nao no que esta sendo construido. Use qualificar()."
        )

    def test_qualificar_usa_o_schema_alvo(self):
        st.definir_schema_alvo("dados_202608")
        try:
            assert st.qualificar("estabelecimento") == '"dados_202608"."estabelecimento"'
        finally:
            st.definir_schema_alvo("public")

    def test_o_default_preserva_o_modo_de_operacao_atual(self):
        # `public` por default para que a parametrizacao nao mude o
        # comportamento antes de o blue/green ser exercitado (item 26). Trocar
        # este valor e o que LIGA o blue/green.
        assert st.schema_alvo() == "public"
