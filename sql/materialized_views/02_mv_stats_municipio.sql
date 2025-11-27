-- =============================================================================
-- mv_stats_municipio - Estatísticas agregadas por Município
-- =============================================================================
-- Tempo estimado de criação: ~5 min
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

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

