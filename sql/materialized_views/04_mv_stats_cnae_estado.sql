-- =============================================================================
-- mv_stats_cnae_estado - Estatísticas detalhadas CNAE x Estado
-- =============================================================================
-- Tempo estimado de criação: ~10 min
-- Periodicidade de refresh recomendada: Semanal
-- =============================================================================

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

