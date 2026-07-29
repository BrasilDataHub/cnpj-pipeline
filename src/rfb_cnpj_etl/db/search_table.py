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

from .schema_target import qualificar
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
# GIN trigram on the normalized name columns (substring search), btrees with
# `text_pattern_ops` for the anchored prefix search, and composite btrees
# aligned with the website's anchor filters.
#
# The two families of text index are NOT redundant — they serve opposite
# shapes of the same column:
#   - GIN trigram answers `LIKE '%TERM%'` (the free-text box);
#   - the `text_pattern_ops` btree answers `LIKE 'TERM%'` (the per-field
#     filters of the advanced search).
# Letting the trigram answer prefixes is what produced the measured timeouts:
# `razao_social_norm ILIKE 'MA%'` returns 11.467.078 candidates from the GIN
# and discards 9.915.193 on recheck, taking 14,0 s against a
# `statement_timeout` of 10 s. See docs/database.md.
#
# `text_pattern_ops` is required because the database collation is
# `en_US.utf8`: a plain btree cannot serve `LIKE` outside the C collation.
# It is also the reason the website must issue `LIKE`, not `ILIKE` — no
# btree opclass serves a case-insensitive match. That costs nothing here:
# both the column and the search term are already `unaccent(upper(...))`.
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
        'name': 'idx_busca_razao_social_prefix',
        'sql': '("razao_social_norm" text_pattern_ops)'
    },
    {
        'name': 'idx_busca_nome_fantasia_prefix',
        'sql': '("nome_fantasia_norm" text_pattern_ops)'
    },
    {
        'name': 'idx_busca_cidade_situacao_cnpj',
        'sql': '("cod_cidade_ibge", "cod_situacao_cadastral", "cnpj_completo")'
    },
    {
        'name': 'idx_busca_cnae_estado_situacao',
        'sql': '("cod_cnae_principal", "cod_estado_ibge", "cod_situacao_cadastral")'
    },
    # Partial: the public hub `/municipio/{cidade}/cnae/{cnae}` lists only
    # active establishments, 27.800.285 of the 72.318.968 rows. Without it the
    # planner picks `idx_busca_cidade_situacao_cnpj` and pushes the CNAE into a
    # Filter, discarding ~59 rows for each one kept — page 100 of the listing
    # measured 22,8 s. `cnpj_completo` is the third column so the scan is Index
    # Only and already ordered, with no sort.
    {
        'name': 'idx_busca_cidade_cnae_ativos',
        'sql': '("cod_cidade_ibge", "cod_cnae_principal", "cnpj_completo") '
               'WHERE "cod_situacao_cadastral" = \'02\''
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
        cur.execute(f'DROP TABLE IF EXISTS {qualificar(build_table)};')

        # 1. CTAS without WAL: durability comes from the SET LOGGED right after
        start = time.time()
        cur.execute(f'CREATE UNLOGGED TABLE {qualificar(build_table)} AS {_SELECT_SOURCE};')
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
        cur.execute(f'ALTER TABLE {qualificar(build_table)} SET LOGGED;')
        print_log(f"  -> SET LOGGED ({time.time() - start:.1f}s)", level="docs")

        # 3. PK + indexes (the *_new table takes no reads; plain CREATE INDEX)
        start = time.time()
        cur.execute(f'ALTER TABLE {qualificar(build_table)} ADD PRIMARY KEY ("cnpj_completo");')
        print_log(f"  -> PK criada ({time.time() - start:.1f}s)", level="docs")

        total = len(SEARCH_TABLE_INDEXES)
        for i, index in enumerate(SEARCH_TABLE_INDEXES, start=1):
            build_name = f"{index['name']}{BUILD_SUFFIX}"
            start = time.time()
            cur.execute(
                f'CREATE INDEX "{build_name}" ON {qualificar(build_table)} {index["sql"]};'
            )
            print_log(
                f"  -> [{i}/{total}] ÍNDICE CRIADO: {index['name']} ({time.time() - start:.1f}s)",
                level="docs"
            )

        cur.execute(f'ANALYZE {qualificar(build_table)};')

        # 4. Atomic swap: readers never see an intermediate state
        conn.autocommit = False
        cur.execute(f'DROP TABLE IF EXISTS {qualificar(SEARCH_TABLE)};')
        cur.execute(f'ALTER TABLE {qualificar(build_table)} RENAME TO "{SEARCH_TABLE}";')
        cur.execute(
            f'ALTER TABLE {qualificar(SEARCH_TABLE)} RENAME CONSTRAINT '
            f'"{_pk_name(build_table)}" TO "{_pk_name(SEARCH_TABLE)}";'
        )
        for index in SEARCH_TABLE_INDEXES:
            cur.execute(
                f'ALTER INDEX {qualificar(index["name"] + BUILD_SUFFIX)} '
                f'RENAME TO "{index["name"]}";'
            )
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
