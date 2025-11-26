-- =============================================================================
-- indexes.sql - Índices Otimizados para Base CNPJ (200M+ registros)
-- =============================================================================
-- Este script cria índices otimizados para suportar as principais consultas
-- realizadas na base de dados de CNPJ da Receita Federal do Brasil.
--
-- IMPORTANTE: Executar APÓS a carga completa dos dados e VACUUM ANALYZE.
--
-- Tipos de índices utilizados:
-- - BTREE: Índice padrão para filtros de igualdade e range
-- - GIN (pg_trgm): Para busca textual com ILIKE '%termo%'
-- - BRIN: Para colunas de data naturalmente ordenadas (economia de espaço)
-- - HASH: Para lookups exatos de alta performance
--
-- Espaço estimado: ~26 GB
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
-- ÍNDICES: ESTABELECIMENTO - Localização
-- =============================================================================
\echo ''
\echo '>>> [1/8] Criando índices de localização (estabelecimento)...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cidade_ibge 
    ON estabelecimento (cod_cidade_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_estado_ibge 
    ON estabelecimento (cod_estado_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_regiao_ibge 
    ON estabelecimento (cod_regiao_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cep 
    ON estabelecimento (cep);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_ddd 
    ON estabelecimento (ddd_telefone_1);

-- Índice composto para filtros região+estado (otimiza consultas hierárquicas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_regiao_estado 
    ON estabelecimento (cod_regiao_ibge, cod_estado_ibge);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - CNAEs
-- =============================================================================
\echo ''
\echo '>>> [2/8] Criando índices de CNAE (estabelecimento)...'

-- CNAE principal (filtro mais usado)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_principal 
    ON estabelecimento (cod_cnae_principal);

-- CNAE + Estado (consulta combinada frequente)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_estado 
    ON estabelecimento (cod_cnae_principal, cod_estado_ibge);

-- CNAE + Cidade (para consultas locais)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_cidade 
    ON estabelecimento (cod_cnae_principal, cod_cidade_ibge);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Datas
-- =============================================================================
\echo ''
\echo '>>> [3/8] Criando índices de datas (BRIN + BTREE)...'

-- BRIN é excelente para datas naturalmente ordenadas (economia de 95% de espaço)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_inicio_brin 
    ON estabelecimento USING BRIN (data_inicio_atividade) 
    WITH (pages_per_range = 32);

-- BTREE para consultas pontuais e ORDER BY
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_inicio 
    ON estabelecimento (data_inicio_atividade);

-- Data de situação cadastral
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_situacao 
    ON estabelecimento (data_situacao_cadastral);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Situação Cadastral / Tipo
-- =============================================================================
\echo ''
\echo '>>> [4/8] Criando índices de situação e tipo...'

-- Situação cadastral (02=Ativa é ~60% dos dados)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_situacao 
    ON estabelecimento (cod_situacao_cadastral);

-- Índice parcial para ATIVAS (otimiza consultas mais comuns)
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
\echo '>>> [5/8] Criando índices de busca textual (GIN)...'

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
\echo '>>> [6/8] Criando índices de contatos...'

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
\echo '>>> [7/8] Criando índices de CNPJ completo...'

-- Índice único para busca exata por CNPJ completo
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnpj_completo 
    ON estabelecimento (cnpj_completo);

-- Hash para lookup ultra-rápido (opcional, para consultas pontuais)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnpj_completo_hash 
    ON estabelecimento USING HASH (cnpj_completo);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO - Compostos (Consultas Reais)
-- =============================================================================
\echo ''
\echo '>>> [8/8] Criando índices compostos...'

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
-- ÍNDICES: EMPRESA
-- =============================================================================
\echo ''
\echo '>>> Criando índices da tabela empresa...'

-- Porte
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_porte 
    ON empresa (cod_porte);

-- Natureza jurídica
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_natureza 
    ON empresa (cod_natureza_juridica);

-- Capital social (range queries)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_capital 
    ON empresa (capital_social);

-- GIN trigram para razão social
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_razao_social_trgm 
    ON empresa USING GIN (razao_social gin_trgm_ops);

-- BTREE para prefixo em razão social
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_razao_social_prefix 
    ON empresa (razao_social varchar_pattern_ops);

-- =============================================================================
-- ÍNDICES: ESTABELECIMENTO_CNAE_SEC (CNAEs Secundários)
-- =============================================================================
\echo ''
\echo '>>> Criando índices da tabela estabelecimento_cnae_sec...'

-- CNAE secundário
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae 
    ON estabelecimento_cnae_sec (cod_cnae);

-- CNPJ completo (FK para estabelecimento)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnpj_completo 
    ON estabelecimento_cnae_sec (cnpj_completo);

-- Covering index para CNAE secundário (evita table lookup)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_covering 
    ON estabelecimento_cnae_sec (cod_cnae) 
    INCLUDE (cnpj_completo);

-- CNAE secundário + localização (requer colunas desnormalizadas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_estado 
    ON estabelecimento_cnae_sec (cod_cnae, cod_estado_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_cidade 
    ON estabelecimento_cnae_sec (cod_cnae, cod_cidade_ibge);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae_regiao 
    ON estabelecimento_cnae_sec (cod_cnae, cod_regiao_ibge);

-- =============================================================================
-- ÍNDICES: SÓCIO
-- =============================================================================
\echo ''
\echo '>>> Criando índices da tabela socio...'

-- CPF/CNPJ do sócio (consultas de vinculação)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_cpf_cnpj 
    ON socio (cnpj_cpf_socio);

-- Nome do sócio (busca textual com GIN trigram)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_trgm 
    ON socio USING GIN (nome_socio gin_trgm_ops);

-- Índice BTREE para prefixo (autocomplete)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_prefix 
    ON socio (nome_socio varchar_pattern_ops);

-- Empresa do sócio (JOIN)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_empresa 
    ON socio (cnpj_basico);

-- =============================================================================
-- FINALIZAÇÃO
-- =============================================================================
\echo ''
\echo '=============================================================='
\echo 'Índices criados com sucesso!'
\echo '=============================================================='
\echo ''
\echo 'Resumo de índices criados:'
\echo '  - Localização (estabelecimento): 6 índices'
\echo '  - CNAEs: 6 índices'
\echo '  - Datas: 3 índices'
\echo '  - Situação/Tipo: 3 índices'
\echo '  - Busca Textual (GIN): 5 índices'
\echo '  - Contatos: 3 índices'
\echo '  - CNPJ Completo: 3 índices'
\echo '  - Compostos: 5 índices'
\echo '  - Empresa: 5 índices'
\echo '  - CNAEs Secundários: 7 índices'
\echo '  - Sócios: 4 índices'
\echo ''
\echo 'Total: ~50 índices | Espaço estimado: ~26 GB'
\echo ''
\timing off

