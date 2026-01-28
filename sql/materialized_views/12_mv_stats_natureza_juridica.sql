-- ============================================================================
-- Materialized View: mv_stats_natureza_juridica
-- ============================================================================
-- Estatísticas de Natureza Jurídica - NÍVEL NACIONAL (Brasil)
--
-- MV agregada para a página principal de listagem de naturezas jurídicas.
-- Resolve o problema de performance da página /brasil/naturezas-juridicas.
--
-- Casos de uso:
--   - Página de listagem nacional (/brasil/naturezas-juridicas)
--   - Dashboard geral de naturezas jurídicas
--   - Totalizadores do Brasil
--   - Rankings nacionais
--
-- Exemplos de queries:
--
--   -- Listagem completa ordenada por total
--   SELECT * FROM mv_stats_natureza_juridica ORDER BY total DESC;
--
--   -- Busca por código ou nome
--   SELECT * FROM mv_stats_natureza_juridica
--   WHERE cod_natureza ILIKE '%213%' OR nome_natureza ILIKE '%empre%';
--
--   -- Sumário geral (totais do Brasil)
--   SELECT
--       COUNT(*) as total_naturezas,
--       SUM(total) as total_empresas,
--       SUM(ativos) as total_ativas
--   FROM mv_stats_natureza_juridica;
--
--   -- Top 5 naturezas jurídicas por taxa de atividade
--   SELECT cod_natureza, nome_natureza, total, ativos,
--          ROUND((ativos::numeric / NULLIF(total, 0)) * 100, 2) as taxa_atividade
--   FROM mv_stats_natureza_juridica
--   WHERE total > 1000
--   ORDER BY taxa_atividade DESC LIMIT 5;
--
-- Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica;
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_natureza_juridica CASCADE;

CREATE MATERIALIZED VIEW mv_stats_natureza_juridica AS
SELECT
    nj.cod_natureza,
    nj.nome_natureza,
    COUNT(DISTINCT e.cnpj_basico) AS total,
    COUNT(DISTINCT CASE
        WHEN est.cod_situacao_cadastral = '02'
        THEN e.cnpj_basico
    END) AS ativos
FROM natureza_juridica nj
LEFT JOIN empresa e ON nj.cod_natureza = e.cod_natureza_juridica
LEFT JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
    AND est.matriz_filial = '1'
GROUP BY nj.cod_natureza, nj.nome_natureza;

-- Índice único para permitir REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_natureza_cod ON mv_stats_natureza_juridica (cod_natureza);

-- Índices para ordenação e busca
CREATE INDEX idx_mv_natureza_total ON mv_stats_natureza_juridica (total DESC);
CREATE INDEX idx_mv_natureza_ativos ON mv_stats_natureza_juridica (ativos DESC);
CREATE INDEX idx_mv_natureza_nome ON mv_stats_natureza_juridica (nome_natureza);

-- Índice para busca por nome (trigram para ILIKE)
CREATE INDEX idx_mv_natureza_nome_trgm ON mv_stats_natureza_juridica
    USING gin (nome_natureza gin_trgm_ops);

-- Estatísticas para otimização do query planner
ANALYZE mv_stats_natureza_juridica;
