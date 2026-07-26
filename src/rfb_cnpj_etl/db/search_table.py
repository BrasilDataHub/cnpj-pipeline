# db/search_table.py

"""
Construction of the lean `busca_estabelecimento` search table.

A narrow denormalized table with one row per establishment and only the
filterable fields of the website search, with the names normalized via
`unaccent(upper(...))` — the accent problem is eliminated at the data level.
Estimated size: 10–12 GB (fits in RAM on the dedicated instance), taking the
disk out of the critical path of the text search.

Recreated on every monthly load through the build-and-swap pattern: the table
is built as `busca_estabelecimento_new`, indexed and analyzed outside the read
path, and swapped with the current one in a single RENAME transaction — zero
read downtime for the website.

Can be run standalone via: python etl.py db search
"""

import time

import psycopg2

from ..utils.logger import print_log

SEARCH_TABLE = "busca_estabelecimento"
BUILD_SUFFIX = "_new"

# unaccent(upper(...)) requires the unaccent extension (REQUIRED_EXTENSIONS).
# The normalization is defensive: the RFB data is already uppercase without
# accents, but the guarantee now comes from the schema, not from the source.
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
        est.ddd_telefone_2,
        unaccent(upper(est.bairro))         AS bairro_norm
    FROM public.estabelecimento est
    LEFT JOIN public.empresa emp ON emp.cnpj_basico = est.cnpj_basico
"""

# Final indexes of the search table (names WITHOUT the build suffix).
# GIN trigram on the normalized name columns (substring search) and
# composite btrees aligned with the website's anchor filters.
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
    Builds (or rebuilds) `busca_estabelecimento` with an atomic swap.

    Steps: UNLOGGED CTAS (fast, no WAL) → SET LOGGED (durability) →
    PK + indexes → ANALYZE → single DROP/RENAME transaction. Idempotent:
    leftovers from interrupted builds are discarded at the start and the swap
    works with or without a current table.
    """
    build_table = f"{SEARCH_TABLE}{BUILD_SUFFIX}"
    conn = None

    try:
        conn = psycopg2.connect(**postgres_config)
        conn.autocommit = True
        cur = conn.cursor()

        print_log(f"CONSTRUINDO TABELA DE BUSCA '{SEARCH_TABLE}'...", level="task")

        # Preconditions: unaccent extension and source tables
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'unaccent';")
        if cur.fetchone() is None:
            raise RuntimeError(
                "Extensão 'unaccent' ausente. Execute antes: python etl.py db init "
                "(ou CREATE EXTENSION unaccent)."
            )
        cur.execute("SELECT to_regclass('public.estabelecimento'), to_regclass('public.empresa');")
        if None in cur.fetchone():
            raise RuntimeError("Tabelas de origem (estabelecimento/empresa) não encontradas.")

        # Discards leftovers from a previous interrupted build
        cur.execute(f'DROP TABLE IF EXISTS public."{build_table}";')

        # 1. CTAS without WAL: durability comes from the SET LOGGED right after
        start = time.time()
        cur.execute(f'CREATE UNLOGGED TABLE public."{build_table}" AS {_SELECT_SOURCE};')
        rows = cur.rowcount
        print_log(f"  -> CTAS concluído: {rows:,} linhas ({time.time() - start:.1f}s)", level="docs")

        # Validation: one row per establishment (the LEFT JOIN neither
        # multiplies nor loses rows — empresa has a PK on cnpj_basico)
        cur.execute("SELECT count(*) FROM public.estabelecimento;")
        source_rows = cur.fetchone()[0]
        if rows != source_rows:
            raise RuntimeError(
                f"Contagem divergente: {rows:,} linhas na tabela de busca vs "
                f"{source_rows:,} em estabelecimento. Build abortado (a tabela vigente permanece)."
            )

        # 2. Durability before indexing (fewer bytes rewritten)
        start = time.time()
        cur.execute(f'ALTER TABLE public."{build_table}" SET LOGGED;')
        print_log(f"  -> SET LOGGED ({time.time() - start:.1f}s)", level="docs")

        # 3. PK + indexes (the *_new table takes no reads; plain CREATE INDEX)
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

        # 4. Atomic swap: readers never see an intermediate state
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
