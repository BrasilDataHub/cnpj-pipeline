# db/advanced_indexes.py

"""
Define índices avançados para otimização de consultas na base CNPJ.

Estes índices são ADICIONAIS aos índices básicos definidos em schema.py.
Incluem:
- GIN (pg_trgm): Para busca textual com ILIKE '%termo%'
- BRIN: Para colunas de data naturalmente ordenadas (economia de espaço)
- HASH: Para lookups exatos de alta performance
- Índices parciais: Para subconjuntos frequentemente consultados
- Índices compostos: Para consultas específicas de negócio

Espaço estimado: ~20 GB (adicional aos índices do ETL)
Tempo estimado de criação: 30-45 minutos (com paralelismo)
"""

# Extensões necessárias para os índices avançados e para a operação do banco:
# - pg_trgm: busca textual com ILIKE '%termo%' (índices GIN trigram)
# - unaccent: normalização de acentos no dado (tabela de busca enxuta, AG13)
# - pg_stat_statements: diagnóstico de queries em produção; a coleta exige
#   shared_preload_libraries=pg_stat_statements na instância (config em infra/),
#   mas o CREATE EXTENSION é seguro mesmo sem o preload.
REQUIRED_EXTENSIONS = ['pg_trgm', 'unaccent', 'pg_stat_statements']

# Configurações de performance para criação de índices
INDEX_CREATION_CONFIG = {
    'max_parallel_maintenance_workers': 4,
    'maintenance_work_mem': '2GB'
}

# Definição dos índices avançados
# Estrutura:
#   name: Nome do índice
#   table: Tabela onde será criado
#   type: Tipo do índice (BTREE, GIN, BRIN, HASH) - default BTREE
#   columns: Lista de colunas
#   ops: Operador especial (ex: gin_trgm_ops, varchar_pattern_ops)
#   where: Cláusula WHERE para índices parciais
#   include: Colunas para INCLUDE (covering index)
#   options: Opções WITH (ex: pages_per_range para BRIN)

ADVANCED_INDEXES = [
    # =========================================================================
    # ESTABELECIMENTO - Localização (complementares)
    # =========================================================================
    # idx_estab_ddd removido (AG9): coberto por idx_estab_ddd1_covering
    # (parcial + covering). Ver docs/index_cleanup.md.
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
    # ESTABELECIMENTO - CNAEs (complementares)
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
    # ESTABELECIMENTO - Datas (BRIN - economia de 95% de espaço)
    # =========================================================================
    {
        'name': 'idx_estab_data_inicio_brin',
        'table': 'estabelecimento',
        'type': 'BRIN',
        'columns': ['data_inicio_atividade'],
        'options': {'pages_per_range': 32}
    },

    # =========================================================================
    # ESTABELECIMENTO - Situação Cadastral / Tipo
    # =========================================================================
    # idx_estab_matriz_filial removido (AG9): cardinalidade 2, inútil como
    # índice isolado. Ver docs/index_cleanup.md.
    {
        'name': 'idx_estab_ativas',
        'table': 'estabelecimento',
        'columns': ['cod_situacao_cadastral'],
        'where': "cod_situacao_cadastral = '02'"
    },

    # =========================================================================
    # ESTABELECIMENTO - Paginação por Cursor (Keyset/Infinite Scroll)
    # Índices parciais para estabelecimentos ativos, ordenados por cnpj_completo.
    # Suportam consultas com WHERE cod_X = ? AND cnpj_completo > ? ORDER BY cnpj_completo
    # sem necessidade de COUNT(*) para paginação.
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
    # ESTABELECIMENTO - Busca Textual (GIN + pg_trgm)
    # =========================================================================
    # idx_estab_nome_fantasia_prefix removido (AG9): com collation C do
    # banco, o btree comum idx_estab_nome_fantasia já atende prefixo —
    # o varchar_pattern_ops era duplicata exata (1,0 GB).
    {
        'name': 'idx_estab_nome_fantasia_trgm',
        'table': 'estabelecimento',
        'type': 'GIN',
        'columns': ['nome_fantasia'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # ESTABELECIMENTO - Contatos
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
    # ESTABELECIMENTO - CNPJ Completo
    # idx_estab_cnpj_completo_hash removido (AG9): a PK btree já resolve
    # igualdade; o hash era duplicata de 2,0 GB sem ganho mensurável.
    # =========================================================================

    # =========================================================================
    # ESTABELECIMENTO - Compostos (Consultas Reais de Negócio)
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
    # EMPRESA (complementares)
    # =========================================================================
    {
        'name': 'idx_empresa_capital',
        'table': 'empresa',
        'columns': ['capital_social']
    },
    # idx_empresa_razao_social_prefix removido (AG9): duplicata exata do
    # btree idx_empresa_razao_social com collation C (3,7 GB).
    {
        'name': 'idx_empresa_razao_social_trgm',
        'table': 'empresa',
        'type': 'GIN',
        'columns': ['razao_social'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # ESTABELECIMENTO_CNAE_SEC (CNAEs Secundários)
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
    # SÓCIO (complementares)
    # =========================================================================
    # idx_socio_nome_prefix removido (AG9): duplicata exata do btree
    # idx_socio_nome com collation C (0,8 GB).
    {
        'name': 'idx_socio_nome_trgm',
        'table': 'socio',
        'type': 'GIN',
        'columns': ['nome_socio'],
        'ops': 'gin_trgm_ops'
    },

    # =========================================================================
    # OTIMIZAÇÃO: ALTA PRIORIDADE
    # Índices para resolver problemas críticos de performance identificados
    # na análise do banco de dados em produção (DATABASE_OPTIMIZATION_REPORT.md)
    # =========================================================================

    # -------------------------------------------------------------------------
    # Índices compostos para TODAS as situações cadastrais (não apenas ativas)
    # Resolve: Consultas de empresas inativas/baixadas que faziam table scan
    # Impacto: Queries de 300-500ms → <10ms
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
    # SIMPLES NACIONAL / MEI - Índices Parciais
    # Resolve: Filtros por regime tributário faziam scan em ~46M registros
    # Impacto: Queries de 500-800ms → <50ms
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
    # OTIMIZAÇÃO: MÉDIA PRIORIDADE
    # Índices compostos para filtros combinados frequentes
    # =========================================================================

    # -------------------------------------------------------------------------
    # Cidade + CNAE + Situação (substitui índices sem situação)
    # Resolve: Filtros combinados CNAE + localidade + situação
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
    # EMPRESA - Índices compostos para JOINs otimizados
    # Resolve: Filtros por porte/natureza jurídica que requerem JOIN
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
    # OTIMIZAÇÃO: BAIXA PRIORIDADE
    # Índices adicionais para casos específicos
    # =========================================================================

    # -------------------------------------------------------------------------
    # DDD com Covering Index (mais eficiente que índice simples)
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
    # Bairro - Busca textual com trigrams
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
    # Email sem "contab" - Índice parcial para prospecção
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_email_prospeccao',
        'table': 'estabelecimento',
        'columns': ['cod_cidade_ibge', 'cnpj_completo'],
        'where': "email IS NOT NULL AND email != '' AND email NOT ILIKE '%contab%'",
        'comment': 'Emails válidos excluindo contabilidades'
    },

    # =========================================================================
    # OTIMIZAÇÃO: SITEMAPS
    # Índices compostos para queries de geração de sitemaps.
    # Complementam os índices existentes que possuem ordem de colunas invertida
    # e não atendem eficientemente filtros que começam por cod_estado_ibge.
    # =========================================================================

    # -------------------------------------------------------------------------
    # Empresas por UF (company.py → _generate_company_urls_for_state)
    # Query: WHERE matriz_filial = '1' AND cod_estado_ibge = %s ORDER BY cnpj_basico
    # Existente idx_estab_estado_ibge é simples; este composto elimina sort.
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_estado_matriz_cnpj',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'matriz_filial', 'cnpj_basico'],
        'comment': 'Sitemap: empresas por UF com ORDER BY cnpj_basico'
    },

    # -------------------------------------------------------------------------
    # CNAEs por estado (cnae.py → _get_state_cnaes)
    # Query: WHERE cod_estado_ibge = %s GROUP BY cod_cnae ...
    # Existente idx_estab_cnae_estado tem ordem invertida (cnae, estado).
    # -------------------------------------------------------------------------
    {
        'name': 'idx_estab_estado_cnae',
        'table': 'estabelecimento',
        'columns': ['cod_estado_ibge', 'cod_cnae_principal'],
        'comment': 'Sitemap: agregação CNAE filtrada por estado'
    },

    # -------------------------------------------------------------------------
    # CNAEs por cidade (cnae.py → _get_city_cnaes)
    # Query: WHERE cod_estado_ibge = %s GROUP BY cod_cidade_ibge, cod_cnae ...
    # Query mais pesada do fluxo; nenhum índice existente cobre este padrão.
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
    Constrói a instrução SQL para criação de um índice baseado na definição.
    
    Args:
        index_def: Dicionário com a definição do índice
        concurrent: Se True, usa CREATE INDEX CONCURRENTLY
        
    Returns:
        String SQL para criação do índice
    """
    name = index_def['name']
    table = index_def['table']
    index_type = index_def.get('type', 'BTREE')
    columns = index_def['columns']
    ops = index_def.get('ops')
    where = index_def.get('where')
    include = index_def.get('include')
    options = index_def.get('options')
    
    # Construir lista de colunas
    if ops:
        # Operador especial (gin_trgm_ops, varchar_pattern_ops)
        cols_str = ', '.join(f'"{col}" {ops}' for col in columns)
    else:
        cols_str = ', '.join(f'"{col}"' for col in columns)
    
    # Montar SQL base
    concurrent_str = 'CONCURRENTLY ' if concurrent else ''
    
    if index_type.upper() == 'BTREE':
        sql = f'CREATE INDEX {concurrent_str}IF NOT EXISTS "{name}" ON public."{table}" ({cols_str})'
    else:
        sql = f'CREATE INDEX {concurrent_str}IF NOT EXISTS "{name}" ON public."{table}" USING {index_type} ({cols_str})'
    
    # Adicionar INCLUDE (covering index)
    if include:
        include_str = ', '.join(f'"{col}"' for col in include)
        sql += f' INCLUDE ({include_str})'
    
    # Adicionar WITH options (ex: BRIN pages_per_range)
    if options:
        opts_str = ', '.join(f'{k} = {v}' for k, v in options.items())
        sql += f' WITH ({opts_str})'
    
    # Adicionar WHERE (índice parcial)
    if where:
        sql += f' WHERE {where}'
    
    sql += ';'
    
    return sql


def get_indexes_by_table() -> dict:
    """
    Agrupa os índices avançados por tabela.
    
    Returns:
        Dict onde chave é nome da tabela e valor é lista de definições de índices
    """
    indexes_by_table = {}
    for idx in ADVANCED_INDEXES:
        table = idx['table']
        if table not in indexes_by_table:
            indexes_by_table[table] = []
        indexes_by_table[table].append(idx)
    return indexes_by_table

