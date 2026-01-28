-- =============================================================================
-- View Materializada: Estatísticas de CNAE por Natureza Jurídica
-- =============================================================================
-- Esta MV pré-calcula a distribuição de CNAEs por natureza jurídica,
-- considerando apenas matrizes (matriz_filial = '1') ativas (cod_situacao_cadastral = '02').
--
-- Executar manualmente no banco companies_pgsql
--
-- Para refresh:
--   REFRESH MATERIALIZED VIEW mv_stats_natureza_juridica_cnae;
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_natureza_juridica_cnae CASCADE;

CREATE MATERIALIZED VIEW mv_stats_natureza_juridica_cnae AS
SELECT
    e.cod_natureza_juridica as cod_natureza,
    est.cod_cnae_principal as cod_cnae,
    COUNT(DISTINCT e.cnpj_basico) as total
FROM empresa e
INNER JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
    AND est.matriz_filial = '1'
    AND est.cod_situacao_cadastral = '02'
INNER JOIN cnae c ON est.cod_cnae_principal = c.cod_cnae
GROUP BY e.cod_natureza_juridica, est.cod_cnae_principal
HAVING COUNT(DISTINCT e.cnpj_basico) > 0;

-- Índices para otimização de consultas
CREATE INDEX idx_mv_nj_cnae_natureza ON mv_stats_natureza_juridica_cnae(cod_natureza);
CREATE INDEX idx_mv_nj_cnae_cnae ON mv_stats_natureza_juridica_cnae(cod_cnae);
CREATE INDEX idx_mv_nj_cnae_total ON mv_stats_natureza_juridica_cnae(total DESC);

-- Índice composto para consultas frequentes
CREATE INDEX idx_mv_nj_cnae_natureza_total ON mv_stats_natureza_juridica_cnae(cod_natureza, total DESC);
