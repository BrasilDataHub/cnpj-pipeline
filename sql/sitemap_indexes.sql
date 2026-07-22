-- =============================================================================
-- sitemap_indexes.sql - Índices otimizados para geração de sitemaps
-- =============================================================================
-- Índices compostos na tabela `estabelecimento` para acelerar as queries
-- mais pesadas do serviço de geração de sitemaps.
--
-- IMPORTANTE:
--   - Usa CREATE INDEX CONCURRENTLY (não bloqueia reads/writes)
--   - Tempo estimado: 10-30 minutos por índice (~60M registros)
--   - Espaço estimado: ~3-5 GB adicionais no total
--   - Idempotente (IF NOT EXISTS)
--
-- Como aplicar:
--   psql -h <host> -U <user> -d dados_cnpj -f sql/sitemap_indexes.sql
-- =============================================================================

\echo '=============================================================='
\echo 'Criando índices otimizados para geração de sitemaps'
\echo '=============================================================='
\echo ''

-- -----------------------------------------------------------------------------
-- 1/3 - Empresas por UF (alta prioridade)
-- Usado em: company.py → _generate_company_urls_for_state()
-- Query: WHERE matriz_filial = '1' AND cod_estado_ibge = %s ORDER BY cnpj_basico
-- Impacto: Elimina full table scan + sort em memória por UF
-- -----------------------------------------------------------------------------
\echo '>>> [1/3] Criando idx_estab_estado_matriz_cnpj ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_estado_matriz_cnpj
ON estabelecimento (cod_estado_ibge, matriz_filial, cnpj_basico);

\echo '    -> idx_estab_estado_matriz_cnpj criado com sucesso'

-- -----------------------------------------------------------------------------
-- 2/3 - CNAEs por estado (alta prioridade)
-- Usado em: cnae.py → _get_state_cnaes()
-- Query: WHERE cod_estado_ibge = %s GROUP BY cod_cnae ... HAVING COUNT(*) >= N
-- Nota: O existente idx_estab_cnae_estado tem ordem invertida (cnae, estado)
-- Impacto: Acelera agregações de CNAE por estado
-- -----------------------------------------------------------------------------
\echo '>>> [2/3] Criando idx_estab_estado_cnae ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_estado_cnae
ON estabelecimento (cod_estado_ibge, cod_cnae_principal);

\echo '    -> idx_estab_estado_cnae criado com sucesso'

-- -----------------------------------------------------------------------------
-- 3/3 - CNAEs por cidade (média prioridade)
-- Usado em: cnae.py → _get_city_cnaes()
-- Query: WHERE cod_estado_ibge = %s GROUP BY cod_cidade_ibge, ..., cod_cnae ...
-- Impacto: Acelera a query mais pesada do fluxo (GROUP BY em 4 colunas)
-- -----------------------------------------------------------------------------
\echo '>>> [3/3] Criando idx_estab_estado_cidade_cnae ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_estado_cidade_cnae
ON estabelecimento (cod_estado_ibge, cod_cidade_ibge, cod_cnae_principal);

\echo '    -> idx_estab_estado_cidade_cnae criado com sucesso'

-- -----------------------------------------------------------------------------
-- Verificação
-- -----------------------------------------------------------------------------
\echo ''
\echo '=============================================================='
\echo 'Índices criados com sucesso!'
\echo '=============================================================='
\echo ''
\echo 'Verificando índices criados:'
\echo ''

SELECT
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) as tamanho
FROM pg_indexes
JOIN pg_class ON pg_class.relname = pg_indexes.indexname
WHERE tablename = 'estabelecimento'
  AND indexname IN (
    'idx_estab_estado_matriz_cnpj',
    'idx_estab_estado_cnae',
    'idx_estab_estado_cidade_cnae'
  )
ORDER BY indexname;

\echo ''
\echo 'Recomendação: Execute ANALYZE estabelecimento; para atualizar as estatísticas.'
\echo ''
