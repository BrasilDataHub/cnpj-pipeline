# db/schema_target.py

"""
O schema de destino do pipeline, e a salvaguarda que torna errar barato.

O PORQUÊ
========

O ciclo mensal de hoje abre com ``DROP TABLE CASCADE`` de todo o schema. Foi
assim que a rodada de 25/07/2026 abortou **após 6h43 sobre um banco já
destruído**: não havia estado intermediário do qual voltar, porque o primeiro
passo tinha eliminado o anterior.

O blue/green por schema resolve isso — carrega-se em ``dados_<N>`` enquanto
``dados_<N-1>`` serve, e a publicação é uma troca de ``search_path``. Mas ele
introduz um risco novo, e este módulo existe por causa dele:

    **um ``public.`` literal esquecido na parametrização escreveria no schema
    que está servindo.**

Há ``public."{table}"`` literal em ``postgres_builder.py``,
``postgres_loader.py``, ``search_table.py``, ``advanced_indexes.py`` e nos
arquivos de ``sql/``. Parametrizar todos é necessário; confiar que a
parametrização foi completa, não.

A SALVAGUARDA
=============

``revogar_escrita_no_schema_vigente()`` retira ``CREATE, INSERT, UPDATE,
DELETE`` do role do pipeline **no schema que está servindo**. Se um literal
sobreviver, o Postgres responde ``permission denied`` e a carga falha — em vez
de corromper, em silêncio, os dados que o site está exibindo naquele instante.

É a diferença entre um erro que custa uma rodada e um erro que custa a base.

Ela roda ANTES de qualquer coisa do ciclo, e não depois: uma salvaguarda
aplicada no fim protege a próxima execução, não esta.
"""

from typing import Optional

import psycopg2

from ..utils.logger import print_log

#: Prefixo dos schemas de dados. O sufixo é o ``load_id`` da carga.
PREFIXO_SCHEMA = "dados"

#: Schema das extensões. Elas saem do ``public`` de propósito: ``pg_trgm``,
#: ``unaccent`` e ``pg_stat_statements`` estão DENTRO do ``public`` hoje, e num
#: desenho em que ``public`` pudesse ser renomeado elas iriam junto — levando
#: consigo todo índice GIN que depende de ``gin_trgm_ops``.
SCHEMA_EXTENSOES = "ext"

#: Schema dos metadados que sobrevivem a qualquer carga.
SCHEMA_META = "meta"

#: Permissões que o role do pipeline NÃO pode ter no schema que está servindo.
PERMISSOES_DE_ESCRITA = ("CREATE", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


#: Schema em que o pipeline ESCREVE nesta execucao. E resolvido uma vez, no
#: bootstrap do ciclo, e todo DDL/DML passa por `qualificar()`.
#:
#: O default e `public` para que a mudanca nao quebre o modo de operacao atual
#: — que ainda e o de produção enquanto o blue/green não for exercitado (item
#: 26). Trocar este valor é o que liga o blue/green.
_SCHEMA_ALVO = "public"


def definir_schema_alvo(schema: str) -> None:
    """Fixa o schema em que esta execucao escreve."""
    global _SCHEMA_ALVO
    _SCHEMA_ALVO = schema


def schema_alvo() -> str:
    return _SCHEMA_ALVO


def qualificar(tabela: str) -> str:
    """
    ``estabelecimento`` -> ``"dados_202608"."estabelecimento"``.

    A UNICA forma de nomear uma tabela no pipeline. Um ``public."x"`` literal
    que escape daqui escreve no schema que esta servindo — e e por isso que o
    REVOKE de ``revogar_escrita_no_schema_vigente()`` existe: com ele, o
    literal falha com ``permission denied`` em vez de corromper.
    """
    return f'"{_SCHEMA_ALVO}"."{tabela}"'


def nome_do_schema(load_id: str) -> str:
    """``202608`` → ``dados_202608``."""
    return f"{PREFIXO_SCHEMA}_{load_id}"


def criar_schemas_de_infraestrutura(conn) -> None:
    """
    Cria ``ext`` e ``meta``, que existem UMA vez e sobrevivem a toda carga.

    As extensões saem do ``public`` porque um dia o ``public`` pode ser
    renomeado ou descartado — e uma extensão que mora nele vai junto. Com
    ``pg_trgm`` fora, todo índice GIN que usa ``gin_trgm_ops`` vira inválido de
    uma vez, e o sintoma é a busca textual voltando aos segundos sem que nada
    no pipeline tenha mudado.
    """
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_EXTENSOES}";')
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_META}";')

        for extensao in ("pg_trgm", "unaccent", "pg_stat_statements"):
            # `IF NOT EXISTS` não move uma extensão já instalada noutro schema;
            # mover é `ALTER EXTENSION ... SET SCHEMA`, e é uma decisão
            # explícita que não deve acontecer no meio de uma carga.
            cur.execute(
                f'CREATE EXTENSION IF NOT EXISTS "{extensao}" SCHEMA "{SCHEMA_EXTENSOES}";'
            )

    conn.commit()
    print_log(f"schemas de infraestrutura prontos: {SCHEMA_EXTENSOES}, {SCHEMA_META}", "SUCCESS")


def revogar_escrita_no_schema_vigente(
    conn, schema_vigente: str, role: Optional[str] = None
) -> None:
    """
    Retira do role do pipeline o direito de ESCREVER no schema que está
    servindo.

    É a rede de segurança do único ponto do plano onde errar custa caro. Com
    ela, um ``public.`` literal que tenha sobrevivido à parametrização produz
    ``permission denied`` — a carga falha, e os dados que o site está exibindo
    continuam intactos.

    Chamada ANTES de qualquer passo do ciclo. Aplicada no fim, protegeria a
    próxima execução e não esta.
    """
    if not schema_vigente:
        print_log("nenhum schema vigente: nada a revogar (primeira carga)", "INFO")
        return

    with conn.cursor() as cur:
        if role is None:
            cur.execute("SELECT current_user;")
            role = cur.fetchone()[0]

        # Superusuário ignora GRANT/REVOKE — a verificação de privilégio é
        # pulada para ele. Avisar é obrigatório: sem isso, a salvaguarda
        # pareceria aplicada e não estaria, que é a pior combinação possível.
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s;", (role,))
        linha = cur.fetchone()

        if linha and linha[0]:
            print_log(
                f"AVISO: o role '{role}' é SUPERUSUÁRIO — o REVOKE não tem efeito sobre ele. "
                f"A salvaguarda contra 'public.' literal NÃO está ativa. "
                f"Use um role dedicado ao pipeline para que ela funcione.",
                "WARNING",
            )
            return

        permissoes = ", ".join(PERMISSOES_DE_ESCRITA)

        cur.execute(f'REVOKE CREATE ON SCHEMA "{schema_vigente}" FROM "{role}";')
        cur.execute(
            f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES '
            f'IN SCHEMA "{schema_vigente}" FROM "{role}";'
        )
        # E o padrão para tabelas FUTURAS: sem isto, uma tabela criada no schema
        # vigente por outro caminho nasceria gravável.
        cur.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema_vigente}" '
            f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM "{role}";'
        )

    conn.commit()
    print_log(
        f"escrita revogada em '{schema_vigente}' para o role '{role}' ({permissoes}). "
        f"Um 'public.' literal agora falha com permission denied em vez de corromper.",
        "SUCCESS",
    )


def devolver_escrita_no_schema(conn, schema: str, role: Optional[str] = None) -> None:
    """
    Devolve a escrita — usado quando o schema deixa de ser o vigente, ou num
    rollback.
    """
    with conn.cursor() as cur:
        if role is None:
            cur.execute("SELECT current_user;")
            role = cur.fetchone()[0]

        cur.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO "{role}";')
        cur.execute(
            f'GRANT INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES '
            f'IN SCHEMA "{schema}" TO "{role}";'
        )
        cur.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO "{role}";'
        )

    conn.commit()
    print_log(f"escrita devolvida em '{schema}' para o role '{role}'", "SUCCESS")


def definir_search_path(conn, schema: str, incluir_extensoes: bool = True) -> None:
    """
    Aponta a sessão para um schema de dados.

    ``ext`` entra no ``search_path`` porque as extensões moram lá: sem ele,
    ``unaccent(...)`` e ``gin_trgm_ops`` deixam de resolver e todo DDL de índice
    textual falha.

    ``public`` fica FORA. É deliberado: com ele no caminho, um ``public.``
    esquecido continuaria resolvendo e a salvaguarda perderia metade da força.
    """
    caminho = f'"{schema}"'

    if incluir_extensoes:
        caminho += f', "{SCHEMA_EXTENSOES}"'

    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {caminho};")

    print_log(f"search_path = {caminho}", "INFO")


def publicar_schema(conn, schema_novo: str, database: str) -> None:
    """
    A publicação: aponta o ``search_path`` PADRÃO do database para o schema
    novo.

    É a operação inteira do blue/green — e é por isso que a indisponibilidade
    cai de 3h30–6h para menos de 5 segundos. As conexões JÁ ABERTAS continuam
    lendo o schema anterior até reconectarem, o que é exatamente o motivo de o
    ``RECONNECT`` do PgBouncer fazer parte do publish: sem ele, elas servem
    dados velhos **sem erro**.
    """
    with conn.cursor() as cur:
        cur.execute(
            f'ALTER DATABASE "{database}" SET search_path TO "{schema_novo}", "{SCHEMA_EXTENSOES}";'
        )

    conn.commit()
    print_log(
        f"publicado: search_path padrão de '{database}' = {schema_novo}, {SCHEMA_EXTENSOES}. "
        f"Conexões abertas seguem no schema anterior até reconectarem (PgBouncer RECONNECT).",
        "SUCCESS",
    )


def schema_vigente(conn, database: str) -> Optional[str]:
    """Schema de dados que o database publica hoje, lido de ``pg_db_role_setting``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT unnest(setconfig)
            FROM pg_db_role_setting s
            JOIN pg_database d ON d.oid = s.setdatabase
            WHERE d.datname = %s;
            """,
            (database,),
        )
        for (config,) in cur.fetchall():
            if config.startswith("search_path="):
                primeiro = config.split("=", 1)[1].split(",")[0].strip().strip('"')

                if primeiro.startswith(PREFIXO_SCHEMA):
                    return primeiro

    return None
