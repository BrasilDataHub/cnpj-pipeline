# db/advanced_indexes.py

"""
Defines advanced indexes for query optimization on the CNPJ dataset.

These indexes are ADDITIONAL to the basic indexes defined in schema.py.
They include:
- GIN (pg_trgm): For text search with ILIKE '%term%'
- BRIN: For naturally ordered date columns (space savings)
- HASH: For high performance exact lookups
- Partial indexes: For frequently queried subsets
- Composite indexes: For specific business queries

Estimated space: ~20 GB (in addition to the ETL indexes)
Estimated creation time: 30-45 minutes (with parallelism)
"""

from .schema_target import qualificar

# Extensions required by the advanced indexes and by the database operation:
# - pg_trgm: text search with ILIKE '%term%' (GIN trigram indexes)
# - unaccent: accent normalization on the data (lean search table, AG13)
# - pg_stat_statements: query diagnostics in production; collection requires
#   shared_preload_libraries=pg_stat_statements on the instance (config in infra/),
#   but the CREATE EXTENSION is safe even without the preload.
REQUIRED_EXTENSIONS = ['pg_trgm', 'unaccent', 'pg_stat_statements']

# Performance settings for index creation
INDEX_CREATION_CONFIG = {
    'max_parallel_maintenance_workers': 4,
    'maintenance_work_mem': '2GB'
}

# Definition of the advanced indexes
# Structure:
#   name: Index name
#   table: Table where it will be created
#   type: Index type (BTREE, GIN, BRIN, HASH) - default BTREE
#   columns: List of columns
#   ops: Special operator (e.g.: gin_trgm_ops, varchar_pattern_ops)
#   where: WHERE clause for partial indexes
#   include: Columns for INCLUDE (covering index)
#   options: WITH options (e.g.: pages_per_range for BRIN)

ADVANCED_INDEXES = [
    # =========================================================================
    # ESTABELECIMENTO - Location (complementary)
    # =========================================================================
    # idx_estab_ddd removed (AG9): covered by idx_estab_ddd1_covering
    # (partial + covering). See docs/index_cleanup.md.
    {
        'name': 'idx_estab_cep',
        'table': 'estabelecimento',
        'columns': ['cep']
    },
    {
        'name': 'idx_estab_regiao_estado',
        'table': 'estabelecimento',
        'columns': ['cod_regiao_ibge', 'cod_estado_ibge']
    },

    # =========================================================================
    # ESTABELECIMENTO - CNAEs (complementary)
    # =========================================================================
    {
        'name': 'idx_estab_cnae_estado',
        'table': 'estabelecimento',
        'columns': ['cod_cnae_principal', 'cod_estado_ibge']
    },
    {
        'name': 'idx_estab_cnae_cidade',
        'table': 'estabelecimento',
        'columns': ['cod_cnae_principal', 'cod_cidade_ibge']
    },

    # =========================================================================
    # ESTABELECIMENTO - Dates (BRIN - 95% space savings)
    # =========================================================================
    {
        'name': 'idx_estab_data_inicio_brin',
        'table': 'estabelecimento',
        'type': 'BRIN',
        'columns': ['data_inicio_atividade'],
        'options': {'pages_per_range': 32}
    },

    # =========================================================================
    # ESTABELECIMENTO - Registration Status / Type
    # =========================================================================
    # idx_estab_matriz_filial removed (AG9): cardinality 2, useless as a
    # standalone index. See docs/index_cleanup.md.
    {
        'name': 'idx_estab_ativas',
        'table': 'estabelecimento',
        'columns': ['cod_situacao_cadastral'],
        'where': "cod_situacao_cadastral = '02'"
    },

    # =========================================================================
    # ESTABELECIMENTO - Cursor Pagination (Keyset/Infinite Scroll)
    # Partial indexes for active establishments, ordered by cnpj_completo.
    # They support queries with WHERE cod_X = ? AND cnpj_completo > ? ORDER BY cnpj_completo
    # without needing COUNT(*) for pagination.
    # =========================================================================
    {
        'name': 'idx_estab_cidade_ativas_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cnpj_completo'],
        'where': "cod_situacao_cadastral = '02'"
    },
    {
        'name': 'idx_estab_estado_ativas_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cnpj_completo'],
        'where': "cod_situacao_cadastral = '02'"
    },
    {
        'name': 'idx_estab_regiao_ativas_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_regiao_ibge', 'cnpj_completo'],
        'where': "cod_situacao_cadastral = '02'"
    },

    # =========================================================================
    # ESTABELECIMENTO - Text Search (GIN + pg_trgm)
    # =========================================================================
    # idx_estab_nome_fantasia_prefix removed (AG9): with the database's C
    # collation, the plain btree idx_estab_nome_fantasia already handles prefixes —
    # the varchar_pattern_ops was an exact duplicate (1.0 GB).
    {
        'name': 'idx_estab_nome_fantasia_trgm',
        'table': 'estabelecimento',
        'type': 'GIN',
        'columns': ['nome_fantasia'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # ESTABELECIMENTO - Contacts
    # =========================================================================
    {
        'name': 'idx_estab_email',
        'table': 'estabelecimento',
        'columns': ['email'],
        'where': "email IS NOT NULL AND email != ''"
    },
    {
        'name': 'idx_estab_telefone',
        'table': 'estabelecimento',
        'columns': ['telefone_1'],
        'where': "telefone_1 IS NOT NULL AND telefone_1 != ''"
    },
    {
        'name': 'idx_estab_email_hash',
        'table': 'estabelecimento',
        'type': 'HASH',
        'columns': ['email']
    },

    # =========================================================================
    # ESTABELECIMENTO - Full CNPJ
    # idx_estab_cnpj_completo_hash removed (AG9): the btree PK already handles
    # equality; the hash was a 2.0 GB duplicate with no measurable gain.
    # =========================================================================

    # =========================================================================
    # ESTABELECIMENTO - Composite (Real Business Queries)
    # =========================================================================
    {
        'name': 'idx_estab_prospeccao',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cnae_principal', 'cod_situacao_cadastral']
    },
    {
        'name': 'idx_estab_local_cnae',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cod_cnae_principal', 'matriz_filial']
    },
    {
        'name': 'idx_estab_novos_estado',
        'table': 'estabelecimento',
        'columns': ['data_inicio_atividade', 'cod_estado_ibge']
    },
    {
        'name': 'idx_estab_leads_email',
        'table': 'estabelecimento',
        'columns': ['cod_cnae_principal', 'cod_situacao_cadastral'],
        'where': "email IS NOT NULL AND email != ''"
    },
    {
        'name': 'idx_estab_temporal',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cnae_principal', 'data_inicio_atividade']
    },

    # =========================================================================
    # EMPRESA (complementary)
    # =========================================================================
    {
        'name': 'idx_empresa_capital',
        'table': 'empresa',
        'columns': ['capital_social']
    },
    # idx_empresa_razao_social_prefix removed (AG9): exact duplicate of the
    # btree idx_empresa_razao_social with C collation (3.7 GB).
    {
        'name': 'idx_empresa_razao_social_trgm',
        'table': 'empresa',
        'type': 'GIN',
        'columns': ['razao_social'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # ESTABELECIMENTO_CNAE_SEC (Secondary CNAEs)
    # =========================================================================
    {
        'name': 'idx_cnae_sec_cnae',
        'table': 'estabelecimento_cnae_sec',
        'columns': ['cod_cnae']
    },
    {
        'name': 'idx_cnae_sec_covering',
        'table': 'estabelecimento_cnae_sec',
        'columns': ['cod_cnae'],
        'include': ['cnpj_completo']
    },
    {
        'name': 'idx_cnae_sec_cnae_estado',
        'table': 'estabelecimento_cnae_sec',
        'columns': ['cod_cnae', 'cod_estado_ibge']
    },
    {
        'name': 'idx_cnae_sec_cnae_cidade',
        'table': 'estabelecimento_cnae_sec',
        'columns': ['cod_cnae', 'cod_cidade_ibge']
    },
    {
        'name': 'idx_cnae_sec_cnae_regiao',
        'table': 'estabelecimento_cnae_sec',
        'columns': ['cod_cnae', 'cod_regiao_ibge']
    },

    # =========================================================================
    # SOCIO (complementary)
    # =========================================================================
    # idx_socio_nome_prefix removed (AG9): exact duplicate of the btree
    # idx_socio_nome with C collation (0.8 GB).
    {
        'name': 'idx_socio_nome_trgm',
        'table': 'socio',
        'type': 'GIN',
        'columns': ['nome_socio'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # OPTIMIZATION: HIGH PRIORITY
    # Indexes to solve critical performance problems identified
    # in the production database analysis (DATABASE_OPTIMIZATION_REPORT.md)
    # =========================================================================

    # -------------------------------------------------------------------------
    # Composite indexes for ALL registration statuses (not only active ones)
    # Solves: Queries for inactive/closed companies that were doing a table scan
    # Impact: Queries from 300-500ms → <10ms
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_cidade_situacao_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cod_situacao_cadastral', 'cnpj_completo'],
        'comment': 'Suporta paginação por cursor para QUALQUER situação cadastral'
    },
    {
        'name': 'idx_estab_estado_situacao_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_situacao_cadastral', 'cnpj_completo'],
        'comment': 'Suporta paginação por cursor para QUALQUER situação cadastral'
    },

    # -------------------------------------------------------------------------
    # SIMPLES NACIONAL / MEI - Partial Indexes
    # Solves: Filters by tax regime were scanning ~46M records
    # Impact: Queries from 500-800ms → <50ms
    # -------------------------------------------------------------------------
    {
        'name': 'idx_simples_opcao_simples',
        'table': 'simples',
        'columns': ['cnpj_basico'],
        'where': "opcao_simples = 'S'",
        'comment': 'Índice parcial para empresas do Simples Nacional'
    },
    {
        'name': 'idx_simples_opcao_mei',
        'table': 'simples',
        'columns': ['cnpj_basico'],
        'where': "opcao_mei = 'S'",
        'comment': 'Índice parcial para MEI'
    },

    # =========================================================================
    # OPTIMIZATION: MEDIUM PRIORITY
    # Composite indexes for frequent combined filters
    # =========================================================================

    # -------------------------------------------------------------------------
    # City + CNAE + Status (replaces indexes without status)
    # Solves: Combined filters CNAE + location + status
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_cidade_cnae_situacao',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cod_cnae_principal', 'cod_situacao_cadastral'],
        'comment': 'Filtros combinados: cidade + CNAE + situação'
    },
    {
        'name': 'idx_estab_estado_cnae_situacao',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cnae_principal', 'cod_situacao_cadastral'],
        'comment': 'Filtros combinados: estado + CNAE + situação'
    },

    # -------------------------------------------------------------------------
    # EMPRESA - Composite indexes for optimized JOINs
    # Solves: Filters by company size/legal nature that require a JOIN
    # -------------------------------------------------------------------------
    {
        'name': 'idx_empresa_porte_cnpj',
        'table': 'empresa',
        'columns': ['cod_porte', 'cnpj_basico'],
        'comment': 'Otimiza JOINs quando filtrando por porte'
    },
    {
        'name': 'idx_empresa_natureza_cnpj',
        'table': 'empresa',
        'columns': ['cod_natureza_juridica', 'cnpj_basico'],
        'comment': 'Otimiza JOINs quando filtrando por natureza jurídica'
    },

    # =========================================================================
    # OPTIMIZATION: LOW PRIORITY
    # Additional indexes for specific cases
    # =========================================================================

    # -------------------------------------------------------------------------
    # DDD with Covering Index (more efficient than a plain index)
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_ddd1_covering',
        'table': 'estabelecimento',
        'columns': ['ddd_telefone_1'],
        'include': ['cnpj_completo', 'cod_cidade_ibge'],
        'where': "ddd_telefone_1 IS NOT NULL AND ddd_telefone_1 != ''",
        'comment': 'Covering index para filtros por DDD'
    },

    # -------------------------------------------------------------------------
    # Neighborhood - Text search with trigrams
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_bairro_trgm',
        'table': 'estabelecimento',
        'type': 'GIN',
        'columns': ['bairro'],
        'ops': 'gin_trgm_ops',
        'comment': 'Busca por bairro com ILIKE'
    },

    # -------------------------------------------------------------------------
    # Email without "contab" - Partial index for prospecting
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_email_prospeccao',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cnpj_completo'],
        'where': "email IS NOT NULL AND email != '' AND email NOT ILIKE '%contab%'",
        'comment': 'Emails válidos excluindo contabilidades'
    },

    # =========================================================================
    # OPTIMIZATION: SITEMAPS
    # Composite indexes for sitemap generation queries.
    # They complement the existing indexes, which have an inverted column order
    # and do not efficiently serve filters starting with cod_estado_ibge.
    # =========================================================================

    # -------------------------------------------------------------------------
    # Companies by state (company.py → _generate_company_urls_for_state)
    # Query: WHERE matriz_filial = '1' AND cod_estado_ibge = %s ORDER BY cnpj_basico
    # The existing idx_estab_estado_ibge is a plain index; this composite one eliminates the sort.
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_estado_matriz_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'matriz_filial', 'cnpj_basico'],
        'comment': 'Sitemap: empresas por UF com ORDER BY cnpj_basico'
    },

    # -------------------------------------------------------------------------
    # CNAEs by state (cnae.py → _get_state_cnaes)
    # Query: WHERE cod_estado_ibge = %s GROUP BY cod_cnae ...
    # The existing idx_estab_cnae_estado has an inverted order (cnae, state).
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_estado_cnae',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cnae_principal'],
        'comment': 'Sitemap: agregação CNAE filtrada por estado'
    },

    # -------------------------------------------------------------------------
    # CNAEs by city (cnae.py → _get_city_cnaes)
    # Query: WHERE cod_estado_ibge = %s GROUP BY cod_cidade_ibge, cod_cnae ...
    # The heaviest query of the flow; no existing index covers this pattern.
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_estado_cidade_cnae',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cidade_ibge', 'cod_cnae_principal'],
        'comment': 'Sitemap: agregação CNAE por cidade filtrada por estado'
    },
]


def build_create_index_sql(index_def: dict, concurrent: bool = True) -> str:
    """
    Builds the SQL statement for creating an index based on its definition.

    Args:
        index_def: Dictionary with the index definition
        concurrent: If True, uses CREATE INDEX CONCURRENTLY

    Returns:
        SQL string for creating the index
    """
    name = index_def['name']
    table = index_def['table']
    index_type = index_def.get('type', 'BTREE')
    columns = index_def['columns']
    ops = index_def.get('ops')
    where = index_def.get('where')
    include = index_def.get('include')
    options = index_def.get('options')

    # Build the column list
    if ops:
        # Special operator (gin_trgm_ops, varchar_pattern_ops)
        cols_str = ', '.join(f'"{col}" {ops}' for col in columns)
    else:
        cols_str = ', '.join(f'"{col}"' for col in columns)

    # Assemble the base SQL
    concurrent_str = 'CONCURRENTLY ' if concurrent else ''

    if index_type.upper() == 'BTREE':
        sql = f'CREATE INDEX {concurrent_str}IF NOT EXISTS "{name}" ON {qualificar(table)} ({cols_str})'
    else:
        sql = f'CREATE INDEX {concurrent_str}IF NOT EXISTS "{name}" ON {qualificar(table)} USING {index_type} ({cols_str})'

    # Add INCLUDE (covering index)
    if include:
        include_str = ', '.join(f'"{col}"' for col in include)
        sql += f' INCLUDE ({include_str})'

    # Add WITH options (e.g.: BRIN pages_per_range)
    if options:
        opts_str = ', '.join(f'{k} = {v}' for k, v in options.items())
        sql += f' WITH ({opts_str})'

    # Add WHERE (partial index)
    if where:
        sql += f' WHERE {where}'

    sql += ';'

    return sql


def get_indexes_by_table() -> dict:
    """
    Groups the advanced indexes by table.

    Returns:
        Dict where the key is the table name and the value is a list of index definitions
    """
    indexes_by_table = {}
    for idx in ADVANCED_INDEXES:
        table = idx['table']
        if table not in indexes_by_table:
            indexes_by_table[table] = []
        indexes_by_table[table].append(idx)
    return indexes_by_table

