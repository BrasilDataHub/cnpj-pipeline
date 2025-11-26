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

# Extensões necessárias para os índices avançados
REQUIRED_EXTENSIONS = ['pg_trgm']

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
    {
        'name': 'idx_estab_cep',
        'table': 'estabelecimento',
        'columns': ['cep']
    },
    {
        'name': 'idx_estab_ddd',
        'table': 'estabelecimento',
        'columns': ['ddd_telefone_1']
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
    {
        'name': 'idx_estab_ativas',
        'table': 'estabelecimento',
        'columns': ['cod_situacao_cadastral'],
        'where': "cod_situacao_cadastral = '02'"
    },
    {
        'name': 'idx_estab_matriz_filial',
        'table': 'estabelecimento',
        'columns': ['matriz_filial']
    },

    # =========================================================================
    # ESTABELECIMENTO - Busca Textual (GIN + pg_trgm)
    # =========================================================================
    {
        'name': 'idx_estab_nome_fantasia_trgm',
        'table': 'estabelecimento',
        'type': 'GIN',
        'columns': ['nome_fantasia'],
        'ops': 'gin_trgm_ops'
    },
    {
        'name': 'idx_estab_nome_fantasia_prefix',
        'table': 'estabelecimento',
        'columns': ['nome_fantasia'],
        'ops': 'varchar_pattern_ops'
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
    # ESTABELECIMENTO - CNPJ Completo (HASH para lookup ultra-rápido)
    # =========================================================================
    {
        'name': 'idx_estab_cnpj_completo_hash',
        'table': 'estabelecimento',
        'type': 'HASH',
        'columns': ['cnpj_completo']
    },

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
    {
        'name': 'idx_empresa_razao_social_trgm',
        'table': 'empresa',
        'type': 'GIN',
        'columns': ['razao_social'],
        'ops': 'gin_trgm_ops'
    },
    {
        'name': 'idx_empresa_razao_social_prefix',
        'table': 'empresa',
        'columns': ['razao_social'],
        'ops': 'varchar_pattern_ops'
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
    {
        'name': 'idx_socio_nome_trgm',
        'table': 'socio',
        'type': 'GIN',
        'columns': ['nome_socio'],
        'ops': 'gin_trgm_ops'
    },
    {
        'name': 'idx_socio_nome_prefix',
        'table': 'socio',
        'columns': ['nome_socio'],
        'ops': 'varchar_pattern_ops'
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

