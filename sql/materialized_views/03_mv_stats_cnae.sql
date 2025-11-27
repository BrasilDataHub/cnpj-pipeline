-- =============================================================================
-- mv_stats_cnae - Estatísticas agregadas por CNAE
-- =============================================================================
-- Tempo estimado de criação: ~3 min
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

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

