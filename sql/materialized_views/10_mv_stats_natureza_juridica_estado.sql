-- ============================================================================
-- Materialized View: mv_stats_natureza_juridica_estado
-- ============================================================================
-- Estatísticas de Natureza Jurídica por ESTADO
--
-- MV pré-agregada para consultas frequentes em nível estadual.
-- Inclui cod_regiao_ibge para facilitar agregações regionais.
--
-- Casos de uso:
--   - Página de listagem de naturezas jurídicas por estado (/estado/{uf}/naturezas-juridicas)
--   - Ranking de estados por natureza jurídica
--   - Comparativo entre estados
--   - Gráficos de distribuição por região
--   - Mapas de calor por UF
--
-- Exemplos de queries:
--
--   -- Ranking de estados por total de MEIs
--   SELECT ie.sigla_uf, ie.nome_estado, m.total, m.ativos,
--          ROUND((m.ativos::numeric / NULLIF(m.total, 0)) * 100, 2) as taxa_atividade
--   FROM mv_stats_natureza_juridica_estado m
--   JOIN ibge_estado ie ON m.cod_estado_ibge = ie.cod_estado_ibge
--   WHERE m.cod_natureza = '2135'
--   ORDER BY m.total DESC;
--
--   -- Agregação por região
--   SELECT ir.nome_regiao, ir.sigla_regiao,
--          SUM(m.total) as total, SUM(m.ativos) as ativos
--   FROM mv_stats_natureza_juridica_estado m
--   JOIN ibge_regiao ir ON m.cod_regiao_ibge = ir.cod_regiao_ibge
--   WHERE m.cod_natureza = '2062'  -- Sociedade Empresária Limitada
--   GROUP BY ir.cod_regiao_ibge, ir.nome_regiao, ir.sigla_regiao
--   ORDER BY total DESC;
--
--   -- Todas as naturezas jurídicas de um estado
--   SELECT nj.nome_natureza, m.total, m.ativos
--   FROM mv_stats_natureza_juridica_estado m
--   JOIN natureza_juridica nj ON m.cod_natureza = nj.cod_natureza
--   WHERE m.cod_estado_ibge = 35  -- São Paulo
--   ORDER BY m.total DESC;
--
--   -- Comparativo Sudeste vs Sul para EIRELIs
--   SELECT ir.nome_regiao, SUM(m.total) as total
--   FROM mv_stats_natureza_juridica_estado m
--   JOIN ibge_regiao ir ON m.cod_regiao_ibge = ir.cod_regiao_ibge
--   WHERE m.cod_natureza = '2305'
--     AND m.cod_regiao_ibge IN (3, 4)  -- SE e S
--   GROUP BY ir.nome_regiao;
--
-- Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica_estado;
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_natureza_juridica_estado CASCADE;

CREATE MATERIALIZED VIEW mv_stats_natureza_juridica_estado AS
SELECT
    nj.cod_natureza,
    ie.cod_regiao_ibge,
    est.cod_estado_ibge,
    COUNT(DISTINCT e.cnpj_basico) AS total,
    COUNT(DISTINCT CASE
        WHEN est.cod_situacao_cadastral = '02'
        THEN e.cnpj_basico
    END) AS ativos
FROM natureza_juridica nj
LEFT JOIN empresa e ON nj.cod_natureza = e.cod_natureza_juridica
LEFT JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
    AND est.matriz_filial = '1'
LEFT JOIN ibge_estado ie ON est.cod_estado_ibge = ie.cod_estado_ibge
WHERE est.cod_estado_ibge IS NOT NULL
GROUP BY nj.cod_natureza, ie.cod_regiao_ibge, est.cod_estado_ibge;

-- Índice único para permitir REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_nj_est_pk
    ON mv_stats_natureza_juridica_estado (cod_natureza, cod_estado_ibge);

-- Índices para consultas frequentes
CREATE INDEX idx_mv_nj_est_natureza ON mv_stats_natureza_juridica_estado (cod_natureza);
CREATE INDEX idx_mv_nj_est_regiao ON mv_stats_natureza_juridica_estado (cod_regiao_ibge);
CREATE INDEX idx_mv_nj_est_estado ON mv_stats_natureza_juridica_estado (cod_estado_ibge);
CREATE INDEX idx_mv_nj_est_total ON mv_stats_natureza_juridica_estado (total DESC);
CREATE INDEX idx_mv_nj_est_ativos ON mv_stats_natureza_juridica_estado (ativos DESC);

-- Índice composto para consultas por região + ordenação
CREATE INDEX idx_mv_nj_est_regiao_total ON mv_stats_natureza_juridica_estado (cod_regiao_ibge, total DESC);

-- Índice composto para consultas de natureza específica ordenadas por total
CREATE INDEX idx_mv_nj_est_nat_total ON mv_stats_natureza_juridica_estado (cod_natureza, total DESC);

-- Estatísticas para otimização do query planner
ANALYZE mv_stats_natureza_juridica_estado;
