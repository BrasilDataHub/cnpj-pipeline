# db/search_table.py

"""
Construção da tabela de busca enxuta `busca_estabelecimento`.

Tabela desnormalizada estreita com uma linha por estabelecimento e apenas os
campos filtráveis da busca do website, com os nomes normalizados via
`unaccent(upper(...))` — o problema de acento é eliminado no nível do dado.
Tamanho estimado: 10–12 GB (cabe em RAM na instância dedicada), tirando o
disco do caminho crítico da busca textual.

Recriação a cada carga mensal pelo padrão build-and-swap: a tabela é
construída como `busca_estabelecimento_new`, indexada e analisada fora do
caminho de leitura, e trocada pela vigente numa única transação de RENAME —
zero downtime de leitura para o website.

Pode ser executado independentemente via: python etl.py db search
"""

import time

import psycopg2

from ..utils.logger import print_log

SEARCH_TABLE = "busca_estabelecimento"
BUILD_SUFFIX = "_new"

# unaccent(upper(...)) exige a extensão unaccent (REQUIRED_EXTENSIONS).
# A normalização é defensiva: o dado da RFB já é caixa alta sem acento,
# mas a garantia passa a ser do schema, não da origem.
_SELECT_SOURCE = """
    SELECT
        est.cnpj_completo,
        est.cnpj_basico,
        unaccent(upper(emp.razao_social))   AS razao_social_norm,
        unaccent(upper(est.nome_fantasia))  AS nome_fantasia_norm,
        est.cod_regiao_ibge,
        est.cod_estado_ibge,
        est.cod_cidade_ibge,
        est.cod_cnae_principal,
        est.cod_situacao_cadastral,
        est.matriz_filial,
        emp.cod_porte,
        emp.cod_natureza_juridica,
        est.data_inicio_atividade,
        est.cep,
        est.ddd_telefone_1,
        unaccent(upper(est.bairro))         AS bairro_norm
    FROM public.estabelecimento est
    LEFT JOIN public.empresa emp ON emp.cnpj_basico = est.cnpj_basico
"""

# Índices finais da tabela de busca (nomes SEM o sufixo de build).
# GIN trigram nas colunas de nome normalizadas (busca por substring) e
# btrees compostos alinhados aos filtros âncora do website.
SEARCH_TABLE_INDEXES = [
    {
        'name': 'idx_busca_razao_social_trgm',
        'sql': 'USING GIN ("razao_social_norm" gin_trgm_ops)'
    },
    {
        'name': 'idx_busca_nome_fantasia_trgm',
        'sql': 'USING GIN ("nome_fantasia_norm" gin_trgm_ops)'
    },
    {
        'name': 'idx_busca_cidade_situacao_cnpj',
        'sql': '("cod_cidade_ibge", "cod_situacao_cadastral", "cnpj_completo")'
    },
    {
        'name': 'idx_busca_cnae_estado_situacao',
        'sql': '("cod_cnae_principal", "cod_estado_ibge", "cod_situacao_cadastral")'
    },
]


def _pk_name(table: str) -> str:
    return f"{table}_pkey"


def build_search_table(postgres_config: dict) -> None:
    """
    Constrói (ou reconstrói) a `busca_estabelecimento` com swap atômico.

    Etapas: CTAS UNLOGGED (rápido, sem WAL) → SET LOGGED (durabilidade) →
    PK + índices → ANALYZE → transação única de DROP/RENAME. Idempotente:
    restos de builds interrompidos são descartados no início e a troca
    funciona com ou sem tabela vigente.
    """
    build_table = f"{SEARCH_TABLE}{BUILD_SUFFIX}"
    conn = None

    try:
        conn = psycopg2.connect(**postgres_config)
        conn.autocommit = True
        cur = conn.cursor()

        print_log(f"CONSTRUINDO TABELA DE BUSCA '{SEARCH_TABLE}'...", level="task")

        # Pré-condições: extensão unaccent e tabelas de origem
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'unaccent';")
        if cur.fetchone() is None:
            raise RuntimeError(
                "Extensão 'unaccent' ausente. Execute antes: python etl.py db init "
                "(ou CREATE EXTENSION unaccent)."
            )
        cur.execute("SELECT to_regclass('public.estabelecimento'), to_regclass('public.empresa');")
        if None in cur.fetchone():
            raise RuntimeError("Tabelas de origem (estabelecimento/empresa) não encontradas.")

        # Descarta resto de build anterior interrompido
        cur.execute(f'DROP TABLE IF EXISTS public."{build_table}";')

        # 1. CTAS sem WAL: a durabilidade vem do SET LOGGED logo em seguida
        start = time.time()
        cur.execute(f'CREATE UNLOGGED TABLE public."{build_table}" AS {_SELECT_SOURCE};')
        rows = cur.rowcount
        print_log(f"  -> CTAS concluído: {rows:,} linhas ({time.time() - start:.1f}s)", level="docs")

        # Validação: uma linha por estabelecimento (LEFT JOIN não multiplica
        # nem perde linhas — empresa é PK em cnpj_basico)
        cur.execute("SELECT count(*) FROM public.estabelecimento;")
        source_rows = cur.fetchone()[0]
        if rows != source_rows:
            raise RuntimeError(
                f"Contagem divergente: {rows:,} linhas na tabela de busca vs "
                f"{source_rows:,} em estabelecimento. Build abortado (a tabela vigente permanece)."
            )

        # 2. Durabilidade antes de indexar (menos bytes reescritos)
        start = time.time()
        cur.execute(f'ALTER TABLE public."{build_table}" SET LOGGED;')
        print_log(f"  -> SET LOGGED ({time.time() - start:.1f}s)", level="docs")

        # 3. PK + índices (a *_new não recebe leitura; CREATE INDEX simples)
        start = time.time()
        cur.execute(f'ALTER TABLE public."{build_table}" ADD PRIMARY KEY ("cnpj_completo");')
        print_log(f"  -> PK criada ({time.time() - start:.1f}s)", level="docs")

        total = len(SEARCH_TABLE_INDEXES)
        for i, index in enumerate(SEARCH_TABLE_INDEXES, start=1):
            build_name = f"{index['name']}{BUILD_SUFFIX}"
            start = time.time()
            cur.execute(
                f'CREATE INDEX "{build_name}" ON public."{build_table}" {index["sql"]};'
            )
            print_log(
                f"  -> [{i}/{total}] ÍNDICE CRIADO: {index['name']} ({time.time() - start:.1f}s)",
                level="docs"
            )

        cur.execute(f'ANALYZE public."{build_table}";')

        # 4. Swap atômico: leitores nunca veem estado intermediário
        conn.autocommit = False
        cur.execute(f'DROP TABLE IF EXISTS public."{SEARCH_TABLE}";')
        cur.execute(f'ALTER TABLE public."{build_table}" RENAME TO "{SEARCH_TABLE}";')
        cur.execute(
            f'ALTER TABLE public."{SEARCH_TABLE}" RENAME CONSTRAINT '
            f'"{_pk_name(build_table)}" TO "{_pk_name(SEARCH_TABLE)}";'
        )
        for index in SEARCH_TABLE_INDEXES:
            cur.execute(f'ALTER INDEX public."{index["name"]}{BUILD_SUFFIX}" RENAME TO "{index["name"]}";')
        conn.commit()
        conn.autocommit = True

        print_log(
            f"TABELA DE BUSCA '{SEARCH_TABLE}' PRONTA ({rows:,} linhas)", level="success"
        )

    except (psycopg2.Error, RuntimeError) as e:
        if conn is not None and not conn.autocommit:
            conn.rollback()
        print_log(f"ERRO AO CONSTRUIR TABELA DE BUSCA: {e}", level="error")
        raise
    finally:
        if conn is not None:
            conn.close()
