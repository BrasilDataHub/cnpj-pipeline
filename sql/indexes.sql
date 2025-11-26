-- =============================================================================
-- indexes.sql - Índices Otimizados para Base CNPJ (200M+ registros)
-- =============================================================================
-- Este script cria índices ADICIONAIS otimizados para suportar consultas
-- avançadas na base de dados de CNPJ da Receita Federal do Brasil.
--
-- IMPORTANTE: 
--   - Executar APÓS a carga completa dos dados (após `db fk` ou `complete`)
--   - O ETL já cria índices básicos em schema.py. Este script adiciona
--     índices especializados para casos de uso específicos.
--
-- Tipos de índices utilizados:
-- - BTREE: Índice padrão para filtros de igualdade e range
-- - GIN (pg_trgm): Para busca textual com ILIKE '%termo%'
-- - BRIN: Para colunas de data naturalmente ordenadas (economia de espaço)
-- - HASH: Para lookups exatos de alta performance
--
-- Espaço estimado: ~20 GB (adicional aos índices do ETL)
-- Tempo estimado de criação: 30-45 minutos (com paralelismo)
-- =============================================================================

\echo '=============================================================='
\echo 'Iniciando criação de índices otimizados...'
\echo '=============================================================='
\timing on

-- =============================================================================
-- PRÉ-REQUISITOS: Habilitar extensão para busca textual
-- =============================================================================
\echo ''
\echo '>>> Habilitando extensão pg_trgm para busca textual...'
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- CONFIGURAÇÕES DE PERFORMANCE PARA CRIAÇÃO DE ÍNDICES
-- =============================================================================
\echo ''
\echo '>>> Configurando parâmetros de performance...'
SET max_parallel_maintenance_workers = 4;
SET maintenance_work_mem = '2GB';

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Localização (complementares)
-- =============================================================================
\echo ''
\echo '>>> [1/9] Criando índices de localização (estabelecimento)...'

-- Nota: idx_estab_cidade_ibge, idx_estab_estado_ibge e idx_estab_regiao_ibge
-- já são criados pelo ETL em schema.py

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cep 
    ON estabelecimento (cep);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_ddd 
    ON estabelecimento (ddd_telefone_1);

-- Índice composto para filtros região+estado (otimiza consultas hierárquicas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_regiao_estado 
    ON estabelecimento (cod_regiao_ibge, cod_estado_ibge);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - CNAEs (complementares)
-- =============================================================================
\echo ''
\echo '>>> [2/9] Criando índices de CNAE compostos (estabelecimento)...'

-- Nota: idx_estab_cnae_principal já é criado pelo ETL em schema.py

-- CNAE + Estado (consulta combinada frequente)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_estado 
    ON estabelecimento (cod_cnae_principal, cod_estado_ibge);

-- CNAE + Cidade (para consultas locais)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_cidade 
    ON estabelecimento (cod_cnae_principal, cod_cidade_ibge);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Datas (complementares)
-- =============================================================================
\echo ''
\echo '>>> [3/9] Criando índices de datas (BRIN)...'

-- Nota: idx_estab_data_inicio e idx_estab_data_situacao já são criados pelo ETL

-- BRIN é excelente para datas naturalmente ordenadas (economia de 95% de espaço)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_inicio_brin 
    ON estabelecimento USING BRIN (data_inicio_atividade) 
    WITH (pages_per_range = 32);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Situação Cadastral / Tipo (complementares)
-- =============================================================================
\echo ''
\echo '>>> [4/9] Criando índices de situação e tipo...'

-- Nota: idx_estab_situacao já é criado pelo ETL em schema.py

-- Índice parcial para ATIVAS (otimiza consultas mais comuns - ~60% dos dados)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_ativas 
    ON estabelecimento (cod_situacao_cadastral) 
    WHERE cod_situacao_cadastral = '02';

-- Matriz/Filial
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_matriz_filial 
    ON estabelecimento (matriz_filial);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Busca Textual (GIN + pg_trgm)
-- =============================================================================
\echo ''
\echo '>>> [5/9] Criando índices de busca textual (GIN)...'

-- Nota: idx_estab_nome_fantasia (BTREE) já é criado pelo ETL

-- GIN trigram para ILIKE '%termo%' em nome_fantasia
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_nome_fantasia_trgm 
    ON estabelecimento USING GIN (nome_fantasia gin_trgm_ops);

-- Índice BTREE para prefixo (LIKE 'termo%' - mais rápido para autocomplete)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_nome_fantasia_prefix 
    ON estabelecimento (nome_fantasia varchar_pattern_ops);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Contatos
-- =============================================================================
\echo ''
\echo '>>> [6/9] Criando índices de contatos...'

-- Email (parcial para não-nulos - economia de espaço)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_email 
    ON estabelecimento (email) 
    WHERE email IS NOT NULL AND email != '';

-- Telefone (parcial para não-nulos)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_telefone 
    ON estabelecimento (telefone_1) 
    WHERE telefone_1 IS NOT NULL AND telefone_1 != '';

-- Hash index para deduplicação por email (lookup exato ultra-rápido)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_email_hash 
    ON estabelecimento USING HASH (email);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - CNPJ Completo
-- =============================================================================
\echo ''
\echo '>>> [7/9] Criando índice HASH para CNPJ completo...'

-- Nota: cnpj_completo já é PRIMARY KEY (BTREE único implícito)
-- Hash para lookup ultra-rápido em consultas de igualdade
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnpj_completo_hash 
    ON estabelecimento USING HASH (cnpj_completo);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Compostos (Consultas Reais)
-- =============================================================================
\echo ''
\echo '>>> [8/9] Criando índices compostos...'

-- Combo: Estado + CNAE + Situação (consulta típica de prospecção)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_prospeccao 
    ON estabelecimento (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- Combo: Cidade + CNAE + Matriz (consulta local)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_local_cnae 
    ON estabelecimento (cod_cidade_ibge, cod_cnae_principal, matriz_filial);

-- Combo: Período + Estado (novos estabelecimentos)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_novos_estado 
    ON estabelecimento (data_inicio_atividade, cod_estado_ibge);

-- Combo: CNAE + Situação + Com Email (lead generation)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_leads_email 
    ON estabelecimento (cod_cnae_principal, cod_situacao_cadastral)
    WHERE email IS NOT NULL AND email != '';

-- Combo: Estado + CNAE + Data (análise temporal)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_temporal 
    ON estabelecimento (cod_estado_ibge, cod_cnae_principal, data_inicio_atividade);

-- =============================================================================
-- ÍNDICES: EMPRESA (complementares)
-- =============================================================================
\echo ''
\echo '>>> [9/9] Criando índices complementares...'

-- Nota: idx_empresa_porte, idx_empresa_natureza e idx_empresa_razao_social
-- já são criados pelo ETL em schema.py

-- Capital social (range queries)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_capital 
    ON empresa (capital_social);

-- GIN trigram para razão social (ILIKE '%termo%')
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_razao_social_trgm 
    ON empresa USING GIN (razao_social gin_trgm_ops);

-- BTREE para prefixo em razão social (autocomplete)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_razao_social_prefix 
    ON empresa (razao_social varchar_pattern_ops);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO_CNAE_SEC (CNAEs Secundários)
-- =============================================================================
\echo ''
\echo '>>> Criando índices da tabela estabelecimento_cnae_sec...'

-- Nota: idx_estab_cnae_sec_cnpj já é criado pelo ETL em schema.py

-- CNAE secundário
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae 
    ON estabelecimento_cnae_sec (cod_cnae);

-- Covering index para CNAE secundário (evita table lookup)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_covering 
    ON estabelecimento_cnae_sec (cod_cnae) 
    INCLUDE (cnpj_completo);

-- CNAE secundário + localização (usa colunas desnormalizadas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_estado 
    ON estabelecimento_cnae_sec (cod_cnae, cod_estado_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_cidade 
    ON estabelecimento_cnae_sec (cod_cnae, cod_cidade_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_regiao 
    ON estabelecimento_cnae_sec (cod_cnae, cod_regiao_ibge);

-- =============================================================================
-- ÍNDICES: SÓCIO (complementares)
-- =============================================================================
\echo ''
\echo '>>> Criando índices da tabela socio...'

-- Nota: idx_socio_empresa, idx_socio_cpf_cnpj e idx_socio_nome
-- já são criados pelo ETL em schema.py

-- Nome do sócio (busca textual com GIN trigram)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_trgm 
    ON socio USING GIN (nome_socio gin_trgm_ops);

-- Índice BTREE para prefixo (autocomplete)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_prefix 
    ON socio (nome_socio varchar_pattern_ops);

-- =============================================================================
-- FINALIZAÇÃO
-- =============================================================================
\echo ''
\echo '=============================================================='
\echo 'Índices adicionais criados com sucesso!'
\echo '=============================================================='
\echo ''
\echo 'Este script criou índices ADICIONAIS aos já criados pelo ETL.'
\echo ''
\echo 'Índices adicionais criados:'
\echo '  - Localização: 3 índices (cep, ddd, região+estado)'
\echo '  - CNAEs compostos: 2 índices'
\echo '  - Datas (BRIN): 1 índice'
\echo '  - Situação/Tipo: 2 índices (ativas parcial, matriz_filial)'
\echo '  - Busca Textual (GIN): 4 índices (nome_fantasia, razão_social, nome_socio)'
\echo '  - Contatos: 3 índices'
\echo '  - CNPJ Completo (HASH): 1 índice'
\echo '  - Compostos: 5 índices (prospecção, local, novos, leads, temporal)'
\echo '  - Empresa: 3 índices (capital, razão_social_trgm, razão_social_prefix)'
\echo '  - CNAEs Secundários: 5 índices'
\echo '  - Sócios: 2 índices (nome_trgm, nome_prefix)'
\echo ''
\echo 'Total: ~31 índices adicionais | Espaço estimado: ~20 GB'
\echo ''
\echo 'Índices já criados pelo ETL (schema.py):'
\echo '  - estabelecimento: 11 índices'
\echo '  - empresa: 4 índices'
\echo '  - socio: 3 índices'
\echo '  - estabelecimento_cnae_sec: 1 índice'
\echo '  - tabelas IBGE: 4 índices'
\echo '  - simples: 1 índice'
\echo ''
\timing off
