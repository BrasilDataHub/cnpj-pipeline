-- =============================================================================
-- materialized_views.sql - Views Materializadas para Base CNPJ (200M+ registros)
-- =============================================================================
-- Este script cria Views Materializadas para pré-computar estatísticas
-- frequentemente acessadas, reduzindo drasticamente o tempo de consultas
-- de agregação de minutos para milissegundos.
--
-- IMPORTANTE: 
--   - Executar APÓS a carga completa dos dados (após `db fk` ou `complete`)
--   - Requer que as tabelas IBGE estejam populadas (ibge_estado, ibge_cidade)
--
-- Periodicidade de refresh recomendada:
-- - mv_stats_estado: Diário (~2 min)
-- - mv_stats_municipio: Diário (~5 min)
-- - mv_stats_cnae: Diário (~3 min)
-- - mv_stats_cnae_estado: Semanal (~10 min)
-- - mv_abertura_periodo: Semanal (~8 min)
-- - mv_top_cnaes_cidade: Semanal (~15 min)
--
-- Espaço estimado: ~2 GB
-- Tempo estimado de criação: 15-20 minutos
-- =============================================================================

\echo '=============================================================='
\echo 'Iniciando criação de Materialized Views...'
\echo '=============================================================='
\timing on

-- =============================================================================
-- MV: ESTATÍSTICAS POR ESTADO
-- =============================================================================
\echo ''
\echo '>>> [1/6] Criando mv_stats_estado...'

DROP MATERIALIZED VIEW IF EXISTS mv_stats_estado CASCADE;

CREATE MATERIALIZED VIEW mv_stats_estado AS
SELECT 
    e.cod_estado_ibge,
    est.sigla_uf,
    est.nome_estado,
    e.cod_regiao_ibge,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY 
    e.cod_estado_ibge, est.sigla_uf, est.nome_estado, e.cod_regiao_ibge;

-- Índice único para REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_stats_estado_pk 
    ON mv_stats_estado (cod_estado_ibge);

\echo '    -> mv_stats_estado criada com sucesso'

-- =============================================================================
-- MV: ESTATÍSTICAS POR MUNICÍPIO
-- =============================================================================
\echo ''
\echo '>>> [2/6] Criando mv_stats_municipio...'

DROP MATERIALIZED VIEW IF EXISTS mv_stats_municipio CASCADE;

CREATE MATERIALIZED VIEW mv_stats_municipio AS
SELECT 
    e.cod_cidade_ibge,
    c.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    e.cod_regiao_ibge,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    MIN(e.data_inicio_atividade) AS primeira_abertura,
    MAX(e.data_inicio_atividade) AS ultima_abertura,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN ibge_cidade c ON e.cod_cidade_ibge = c.cod_cidade_ibge
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY 
    e.cod_cidade_ibge, c.nome_cidade, 
    e.cod_estado_ibge, est.sigla_uf, 
    e.cod_regiao_ibge;

-- Índices para consultas frequentes
CREATE UNIQUE INDEX idx_mv_stats_municipio_pk 
    ON mv_stats_municipio (cod_cidade_ibge);
CREATE INDEX idx_mv_stats_municipio_estado 
    ON mv_stats_municipio (cod_estado_ibge);
CREATE INDEX idx_mv_stats_municipio_total 
    ON mv_stats_municipio (total_estabelecimentos DESC);

\echo '    -> mv_stats_municipio criada com sucesso'

-- =============================================================================
-- MV: ESTATÍSTICAS POR CNAE
-- =============================================================================
\echo ''
\echo '>>> [3/6] Criando mv_stats_cnae...'

DROP MATERIALIZED VIEW IF EXISTS mv_stats_cnae CASCADE;

CREATE MATERIALIZED VIEW mv_stats_cnae AS
SELECT 
    e.cod_cnae_principal,
    c.nome_cnae,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(DISTINCT e.cod_estado_ibge) AS estados_presentes,
    COUNT(DISTINCT e.cod_cidade_ibge) AS cidades_presentes,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
GROUP BY e.cod_cnae_principal, c.nome_cnae;

-- Índices
CREATE UNIQUE INDEX idx_mv_stats_cnae_pk 
    ON mv_stats_cnae (cod_cnae_principal);
CREATE INDEX idx_mv_stats_cnae_total 
    ON mv_stats_cnae (total_estabelecimentos DESC);

\echo '    -> mv_stats_cnae criada com sucesso'

-- =============================================================================
-- MV: ESTATÍSTICAS POR CNAE + ESTADO (Detalhada)
-- =============================================================================
\echo ''
\echo '>>> [4/6] Criando mv_stats_cnae_estado...'

DROP MATERIALIZED VIEW IF EXISTS mv_stats_cnae_estado CASCADE;

CREATE MATERIALIZED VIEW mv_stats_cnae_estado AS
SELECT 
    e.cod_cnae_principal,
    e.cod_estado_ibge,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes
FROM estabelecimento e
GROUP BY e.cod_cnae_principal, e.cod_estado_ibge;

-- Índices
CREATE UNIQUE INDEX idx_mv_stats_cnae_estado_pk 
    ON mv_stats_cnae_estado (cod_cnae_principal, cod_estado_ibge);
CREATE INDEX idx_mv_stats_cnae_estado_cnae 
    ON mv_stats_cnae_estado (cod_cnae_principal);
CREATE INDEX idx_mv_stats_cnae_estado_estado 
    ON mv_stats_cnae_estado (cod_estado_ibge);

\echo '    -> mv_stats_cnae_estado criada com sucesso'

-- =============================================================================
-- MV: EMPRESAS POR PERÍODO DE ABERTURA
-- =============================================================================
\echo ''
\echo '>>> [5/6] Criando mv_abertura_periodo...'

DROP MATERIALIZED VIEW IF EXISTS mv_abertura_periodo CASCADE;

CREATE MATERIALIZED VIEW mv_abertura_periodo AS
SELECT 
    DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
    e.cod_estado_ibge,
    COUNT(*) AS total_aberturas,
    COUNT(DISTINCT e.cnpj_basico) AS empresas_unicas,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos
FROM estabelecimento e
WHERE e.data_inicio_atividade IS NOT NULL
  AND e.data_inicio_atividade >= '2000-01-01'
GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), e.cod_estado_ibge;

-- Índices
CREATE UNIQUE INDEX idx_mv_abertura_pk 
    ON mv_abertura_periodo (mes_abertura, cod_estado_ibge);
CREATE INDEX idx_mv_abertura_mes 
    ON mv_abertura_periodo (mes_abertura);
CREATE INDEX idx_mv_abertura_estado 
    ON mv_abertura_periodo (cod_estado_ibge);

\echo '    -> mv_abertura_periodo criada com sucesso'

-- =============================================================================
-- MV: TOP CNAEs POR CIDADE (Para autocomplete e dashboards)
-- =============================================================================
\echo ''
\echo '>>> [6/6] Criando mv_top_cnaes_cidade...'

DROP MATERIALIZED VIEW IF EXISTS mv_top_cnaes_cidade CASCADE;

CREATE MATERIALIZED VIEW mv_top_cnaes_cidade AS
WITH ranked_cnaes AS (
    SELECT 
        e.cod_cidade_ibge,
        e.cod_cnae_principal,
        c.nome_cnae,
        COUNT(*) AS total,
        ROW_NUMBER() OVER (
            PARTITION BY e.cod_cidade_ibge 
            ORDER BY COUNT(*) DESC
        ) AS ranking
    FROM estabelecimento e
    JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
    WHERE e.cod_situacao_cadastral = '02'
    GROUP BY e.cod_cidade_ibge, e.cod_cnae_principal, c.nome_cnae
    HAVING COUNT(*) >= 10
)
SELECT 
    cod_cidade_ibge,
    cod_cnae_principal,
    nome_cnae,
    total,
    ranking
FROM ranked_cnaes
WHERE ranking <= 20;  -- Top 20 CNAEs por cidade

-- Índices
CREATE INDEX idx_mv_top_cnaes_cidade 
    ON mv_top_cnaes_cidade (cod_cidade_ibge, ranking);
CREATE INDEX idx_mv_top_cnaes_cidade_cnae 
    ON mv_top_cnaes_cidade (cod_cnae_principal);

\echo '    -> mv_top_cnaes_cidade criada com sucesso'

-- =============================================================================
-- FUNÇÃO: REFRESH DE TODAS AS MVs
-- =============================================================================
\echo ''
\echo '>>> Criando função de refresh...'

CREATE OR REPLACE FUNCTION refresh_all_mvs()
RETURNS TABLE (
    mv_name TEXT,
    status TEXT,
    duration INTERVAL
) AS $$
DECLARE
    start_time TIMESTAMP;
    end_time TIMESTAMP;
BEGIN
    -- MVs menores primeiro (mais rápido)
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_estado;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_estado';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_cnae';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    -- MVs maiores
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_municipio;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_municipio';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae_estado;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_cnae_estado';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_abertura_periodo;
    end_time := clock_timestamp();
    mv_name := 'mv_abertura_periodo';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_top_cnaes_cidade;
    end_time := clock_timestamp();
    mv_name := 'mv_top_cnaes_cidade';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

EXCEPTION WHEN OTHERS THEN
    mv_name := 'ERROR';
    status := SQLERRM;
    duration := NULL;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_all_mvs() IS 
'Atualiza todas as Materialized Views de estatísticas.
Recomendado executar quinzenalmente (dias 1 e 15) às 03:00.
Uso: SELECT * FROM refresh_all_mvs();';

-- =============================================================================
-- FINALIZAÇÃO
-- =============================================================================
\echo ''
\echo '=============================================================='
\echo 'Materialized Views criadas com sucesso!'
\echo '=============================================================='
\echo ''
\echo 'Views criadas:'
\echo '  - mv_stats_estado: Estatísticas agregadas por estado'
\echo '  - mv_stats_municipio: Estatísticas agregadas por município'
\echo '  - mv_stats_cnae: Estatísticas agregadas por CNAE'
\echo '  - mv_stats_cnae_estado: Estatísticas detalhadas CNAE x Estado'
\echo '  - mv_abertura_periodo: Aberturas por mês/estado (desde 2000)'
\echo '  - mv_top_cnaes_cidade: Top 20 CNAEs por cidade'
\echo ''
\echo 'Para atualizar todas as MVs, execute:'
\echo '  SELECT * FROM refresh_all_mvs();'
\echo ''
\echo 'Periodicidade recomendada:'
\echo '  - Refresh completo: Quinzenal (dias 1 e 15, às 03:00)'
\echo '  - Refresh incremental: Diário para stats_estado e stats_cnae'
\echo ''
\timing off
