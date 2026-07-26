# db/schema.py

"""
Defines the complete database schema for the CNPJ dataset, including the mapping
from source files to target tables.

This consolidated structure is the "Source of Truth" and makes it easier to
programmatically create tables, indexes, constraints and the data loading logic.
"""

SCHEMA = {
    'cnae': {
        'source_file_stem': 'Cnaes',
        'columns': [
            ('cod_cnae', 'VARCHAR(7) PRIMARY KEY'),
            ('nome_cnae', 'VARCHAR(200) NOT NULL')
        ]
    },
    'motivo': {
        'source_file_stem': 'Motivos',
        'columns': [
            ('cod_motivo', 'VARCHAR(2) PRIMARY KEY'),
            ('nome_motivo', 'VARCHAR(100) NOT NULL')
        ]
    },
    'municipio_rfb': {
        'source_file_stem': 'Municipios',
        # NOTE: The RFB Municipios.zip file has only 2 columns: cod_municipio and nome_municipio.
        # The uf column does NOT exist in the original file.
        'columns': [
            ('cod_municipio', 'VARCHAR(7) PRIMARY KEY'),
            ('nome_municipio', 'VARCHAR(120) NOT NULL')
        ]
    },
    'ibge_regiao': {
        'source_file_stem': 'RegioesIbge',
        'columns': [
            ('cod_regiao_ibge', 'INTEGER PRIMARY KEY'),
            ('sigla_regiao', 'VARCHAR(2) UNIQUE'),
            ('nome_regiao', 'VARCHAR(50) NOT NULL')
        ],
        'indexes': [
            {'name': 'idx_ibge_regiao_sigla', 'columns': ['sigla_regiao']}
        ]
    },
    'ibge_estado': {
        'source_file_stem': 'EstadosIbge',
        'columns': [
            ('cod_estado_ibge', 'INTEGER PRIMARY KEY'),
            ('sigla_uf', 'VARCHAR(2) UNIQUE NOT NULL'),
            ('nome_estado', 'VARCHAR(100) NOT NULL'),
            ('latitude', 'NUMERIC(9,6)'),
            ('longitude', 'NUMERIC(9,6)'),
            ('cod_regiao_ibge', 'INTEGER NOT NULL')
        ],
        'foreign_keys': [
            {'columns': ['cod_regiao_ibge'], 'references': 'ibge_regiao(cod_regiao_ibge)'}
        ],
        'indexes': [
            {'name': 'idx_ibge_estado_sigla', 'columns': ['sigla_uf']},
            {'name': 'idx_ibge_estado_regiao', 'columns': ['cod_regiao_ibge']}
        ]
    },
    'ibge_cidade': {
        'source_file_stem': 'CidadesIbge',
        'columns': [
            ('cod_cidade_ibge', 'INTEGER PRIMARY KEY'),
            ('nome_cidade', 'VARCHAR(120) NOT NULL'),
            ('latitude', 'NUMERIC(9,6)'),
            ('longitude', 'NUMERIC(9,6)'),
            ('capital', 'BOOLEAN'),
            ('cod_estado_ibge', 'INTEGER NOT NULL'),
            ('cod_municipio', 'VARCHAR(7) UNIQUE'),
            ('ddd', 'VARCHAR(3)'),
            ('fuso_horario', 'VARCHAR(50)')
        ],
        'foreign_keys': [
            {'columns': ['cod_estado_ibge'], 'references': 'ibge_estado(cod_estado_ibge)'}
        ],
        'indexes': [
            {'name': 'idx_ibge_cidade_estado', 'columns': ['cod_estado_ibge']},
            {'name': 'idx_ibge_cidade_municipio', 'columns': ['cod_municipio']}
        ]
    },
    'natureza_juridica': {
        'source_file_stem': 'Naturezas',
        'columns': [
            ('cod_natureza', 'VARCHAR(4) PRIMARY KEY'),
            ('nome_natureza', 'VARCHAR(200) NOT NULL')
        ]
    },
    'pais': {
        'source_file_stem': 'Paises',
        'columns': [
            ('cod_pais', 'VARCHAR(3) PRIMARY KEY'),
            ('nome_pais', 'VARCHAR(60) NOT NULL')
        ]
    },
    'qualificacao_socio': {
        'source_file_stem': 'Qualificacoes',
        'columns': [
            ('cod_qualificacao', 'VARCHAR(2) PRIMARY KEY'),
            ('nome_qualificacao', 'VARCHAR(200) NOT NULL')
        ]
    },
    'empresa': {
        'source_file_stem': 'Empresas',
        'columns': [
            ('cnpj_basico', 'VARCHAR(8)'),
            ('razao_social', 'VARCHAR(200)'),
            ('cod_natureza_juridica', 'VARCHAR(4) NOT NULL'),
            ('cod_qualificacao_responsavel', 'VARCHAR(2) NOT NULL'),
            ('capital_social', 'NUMERIC(16,2) NOT NULL'),
            ('cod_porte', 'VARCHAR(2)'),
            ('ente_federativo_responsavel', 'VARCHAR(100)')
        ],
        'primary_key': ['cnpj_basico'],
        'foreign_keys': [
            {'columns': ['cod_natureza_juridica'], 'references': 'natureza_juridica(cod_natureza)'},
            {'columns': ['cod_qualificacao_responsavel'], 'references': 'qualificacao_socio(cod_qualificacao)'}
        ],
        'indexes': [
            {'name': 'idx_empresa_cnpj', 'columns': ['cnpj_basico']},
            # idx_empresa_razao_social (3.7 GB): CONDITIONAL removal — the
            # website still sorts by razao_social (sort=corporate_name);
            # only remove it once no ORDER BY razao_social survives
            # (post-AG14). See docs/index_cleanup.md.
            {'name': 'idx_empresa_razao_social', 'columns': ['razao_social']},
            {'name': 'idx_empresa_natureza', 'columns': ['cod_natureza_juridica']},
            {'name': 'idx_empresa_porte', 'columns': ['cod_porte']}
        ]
    },
    'estabelecimento': {
        'source_file_stem': 'Estabelecimentos',
        'columns': [
            ('cnpj_basico', 'VARCHAR(8) NOT NULL'),
            ('cnpj_ordem', 'VARCHAR(4) NOT NULL'),
            ('cnpj_dv', 'VARCHAR(2) NOT NULL'),
            ('cnpj_completo', 'CHAR(14) NOT NULL'),  # Computed in Python during load
            ('matriz_filial', 'VARCHAR(1) NOT NULL'),
            ('nome_fantasia', 'VARCHAR(60)'),
            ('cod_situacao_cadastral', 'VARCHAR(2) NOT NULL'),
            ('data_situacao_cadastral', 'DATE'),
            ('cod_motivo_situacao_cadastral', 'VARCHAR(2) NOT NULL'),
            ('nome_cidade_exterior', 'VARCHAR(60)'),
            ('cod_pais', 'VARCHAR(3)'),
            ('data_inicio_atividade', 'DATE NOT NULL'),
            ('cod_cnae_principal', 'VARCHAR(7) NOT NULL'),
            ('cod_cnae_secundario', 'TEXT'),
            ('tipo_logradouro', 'VARCHAR(20)'),
            ('logradouro', 'VARCHAR(60)'),
            ('numero', 'VARCHAR(6)'),
            ('complemento', 'VARCHAR(200)'),
            ('bairro', 'VARCHAR(60)'),
            ('cep', 'VARCHAR(8)'),
            ('uf', 'VARCHAR(2) NOT NULL'),
            ('cod_municipio', 'VARCHAR(7)'),
            ('ddd_telefone_1', 'VARCHAR(4)'),
            ('telefone_1', 'VARCHAR(10)'),
            ('ddd_telefone_2', 'VARCHAR(4)'),
            ('telefone_2', 'VARCHAR(10)'),
            ('ddd_fax', 'VARCHAR(4)'),
            ('fax', 'VARCHAR(10)'),
            ('email', 'TEXT'),
            ('situacao_especial', 'VARCHAR(100)'),
            ('data_situacao_especial', 'DATE'),
            ('cod_regiao_ibge', 'INTEGER'),
            ('cod_estado_ibge', 'INTEGER'),
            ('cod_cidade_ibge', 'INTEGER')
        ],
        'primary_key': ['cnpj_completo'],  # Simplified PK using cnpj_completo
        'foreign_keys': [
            {'columns': ['cnpj_basico'], 'references': 'empresa(cnpj_basico)'},
            {'columns': ['cod_cnae_principal'], 'references': 'cnae(cod_cnae)'},
            {'columns': ['cod_pais'], 'references': 'pais(cod_pais)'},
            {'columns': ['cod_motivo_situacao_cadastral'], 'references': 'motivo(cod_motivo)'},
            {'columns': ['cod_regiao_ibge'], 'references': 'ibge_regiao(cod_regiao_ibge)'},
            {'columns': ['cod_estado_ibge'], 'references': 'ibge_estado(cod_estado_ibge)'},
            {'columns': ['cod_cidade_ibge'], 'references': 'ibge_cidade(cod_cidade_ibge)'}
        ],
        # Redundant indexes removed in 2026-07 (AG9, ~3 GB in this list):
        # idx_estab_cnae_principal, idx_estab_data_inicio, idx_estab_situacao,
        # idx_estab_regiao_ibge, idx_estab_estado_ibge and idx_estab_cidade_ibge
        # were exact prefixes of existing composite indexes (or had
        # selectivity too low to justify 0.5 GB each). Rationale and
        # removal protocol in production: docs/index_cleanup.md.
        'indexes': [
            {'name': 'idx_estab_empresa', 'columns': ['cnpj_basico']},
            {'name': 'idx_estab_nome_fantasia', 'columns': ['nome_fantasia']},
            {'name': 'idx_estab_data_situacao', 'columns': ['data_situacao_cadastral']},
            {'name': 'idx_estab_municipio', 'columns': ['cod_municipio']},
            {'name': 'idx_estab_uf_municipio', 'columns': ['uf', 'cod_municipio']}
        ]
    },
    'simples': {
        'source_file_stem': 'Simples',
        'columns': [
            ('cnpj_basico', 'VARCHAR(8)'),
            ('opcao_simples', 'VARCHAR(1)'),
            ('data_opcao_simples', 'DATE'),
            ('data_exclusao_simples', 'DATE'),
            ('opcao_mei', 'VARCHAR(1)'),
            ('data_opcao_mei', 'DATE'),
            ('data_exclusao_mei', 'DATE')
        ],
        'primary_key': ['cnpj_basico'],  # PK to guarantee uniqueness and performance
        'foreign_keys': [
            {'columns': ['cnpj_basico'], 'references': 'empresa(cnpj_basico)'}
        ],
        'indexes': [
            # Composite index for queries that combine option + cnpj
            {'name': 'idx_simples_opcoes', 'columns': ['opcao_simples', 'opcao_mei', 'cnpj_basico']}
        ]
        # NOTE: Partial indexes for opcao_simples='S' and opcao_mei='S'
        # are defined in advanced_indexes.py
    },
    'socio': {
        'source_file_stem': 'Socios',
        'columns': [
            ('cnpj_basico', 'VARCHAR(8) NOT NULL'),
            ('identificador_socio', 'VARCHAR(1) NOT NULL'),
            ('nome_socio', 'VARCHAR(200)'),
            ('cnpj_cpf_socio', 'VARCHAR(14)'),
            ('cod_qualificacao_socio', 'VARCHAR(2) NOT NULL'),
            ('data_entrada_sociedade', 'DATE NOT NULL'),
            ('cod_pais', 'VARCHAR(3)'),
            ('cpf_representante_legal', 'VARCHAR(11)'),
            ('nome_representante_legal', 'VARCHAR(100)'),
            ('cod_qualificacao_representante_legal', 'VARCHAR(2)'),
            ('cod_faixa_etaria', 'VARCHAR(1) NOT NULL')
        ],
        'foreign_keys': [
            {'columns': ['cnpj_basico'], 'references': 'empresa(cnpj_basico)'},
            {'columns': ['cod_pais'], 'references': 'pais(cod_pais)'},
            {'columns': ['cod_qualificacao_socio'], 'references': 'qualificacao_socio(cod_qualificacao)'},
            {'columns': ['cod_qualificacao_representante_legal'], 'references': 'qualificacao_socio(cod_qualificacao)'}
        ],
        'indexes': [
            {'name': 'idx_socio_empresa', 'columns': ['cnpj_basico']},
            {'name': 'idx_socio_cpf_cnpj', 'columns': ['cnpj_cpf_socio']},
            {'name': 'idx_socio_nome', 'columns': ['nome_socio']}
        ]
    },

    'estabelecimento_cnae_sec': {
        'source_file_stem': 'Estabelecimentos',
        'columns': [
            ('cnpj_completo', 'CHAR(14) NOT NULL'),  # Computed in Python during load
            ('cod_cnae', 'VARCHAR(7) NOT NULL'),
            # Denormalized columns to avoid expensive JOINs
            ('cod_regiao_ibge', 'SMALLINT'),
            ('cod_estado_ibge', 'SMALLINT'),
            ('cod_cidade_ibge', 'INTEGER'),
            ('data_inicio_atividade', 'DATE')
        ],
        'foreign_keys': [
            {'columns': ['cnpj_completo'], 'references': 'estabelecimento(cnpj_completo)'},
            {'columns': ['cod_cnae'], 'references': 'cnae(cod_cnae)'}
        ],
        'indexes': [
            {'name': 'idx_estab_cnae_sec_cnpj', 'columns': ['cnpj_completo']}
        ]
    }
}
