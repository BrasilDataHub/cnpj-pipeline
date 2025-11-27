-- =============================================================================
-- mv_stats_estado - Estatísticas agregadas por Estado
-- =============================================================================
-- Tempo estimado de criação: ~2 min
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

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

