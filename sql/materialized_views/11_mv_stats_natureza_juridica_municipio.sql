-- ============================================================================
-- Materialized View: mv_stats_natureza_juridica_municipio
-- ============================================================================
-- Estatísticas de Natureza Jurídica por MUNICÍPIO (granularidade máxima)
--
-- Esta é a MV de base para todas as análises geográficas de natureza jurídica.
-- Permite agregações em qualquer nível: município, estado, região ou nacional.
--
-- Casos de uso:
--   - Ranking de naturezas jurídicas por município
--   - Comparativo entre municípios de um estado
--   - Distribuição geográfica de tipos de empresa
--   - Gráficos de concentração por localidade
--   - Relatórios detalhados por cidade
--
-- Exemplos de queries:
--
--   -- Top 10 municípios com mais MEIs (cod_natureza = '2135')
--   SELECT ic.nome_cidade, ie.sigla_uf, m.total, m.ativos
--   FROM mv_stats_natureza_juridica_municipio m
--   JOIN ibge_cidade ic ON m.cod_cidade_ibge = ic.cod_cidade_ibge
--   JOIN ibge_estado ie ON m.cod_estado_ibge = ie.cod_estado_ibge
--   WHERE m.cod_natureza = '2135'
--   ORDER BY m.total DESC LIMIT 10;
--
--   -- Todas as naturezas jurídicas de um município específico
--   SELECT nj.nome_natureza, m.total, m.ativos
--   FROM mv_stats_natureza_juridica_municipio m
--   JOIN natureza_juridica nj ON m.cod_natureza = nj.cod_natureza
--   WHERE m.cod_cidade_ibge = 3550308  -- São Paulo
--   ORDER BY m.total DESC;
--
--   -- Comparativo entre capitais
--   SELECT ic.nome_cidade, ie.sigla_uf, SUM(m.total) as total, SUM(m.ativos) as ativos
--   FROM mv_stats_natureza_juridica_municipio m
--   JOIN ibge_cidade ic ON m.cod_cidade_ibge = ic.cod_cidade_ibge
--   JOIN ibge_estado ie ON m.cod_estado_ibge = ie.cod_estado_ibge
--   WHERE ic.capital = true
--   GROUP BY ic.nome_cidade, ie.sigla_uf
--   ORDER BY total DESC;
--
-- Refresh: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica_municipio;
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_natureza_juridica_municipio CASCADE;

CREATE MATERIALIZED VIEW mv_stats_natureza_juridica_municipio AS
SELECT
    nj.cod_natureza,
    est.cod_estado_ibge,
    est.cod_cidade_ibge,
    COUNT(DISTINCT e.cnpj_basico) AS total,
    COUNT(DISTINCT CASE
        WHEN est.cod_situacao_cadastral = '02'
        THEN e.cnpj_basico
    END) AS ativos
FROM natureza_juridica nj
LEFT JOIN empresa e ON nj.cod_natureza = e.cod_natureza_juridica
LEFT JOIN estabelecimento est ON e.cnpj_basico = est.cnpj_basico
    AND est.matriz_filial = '1'
WHERE est.cod_cidade_ibge IS NOT NULL
GROUP BY nj.cod_natureza, est.cod_estado_ibge, est.cod_cidade_ibge;

-- Índice único para permitir REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_nj_mun_pk
    ON mv_stats_natureza_juridica_municipio (cod_natureza, cod_cidade_ibge);

-- Índices para consultas frequentes
CREATE INDEX idx_mv_nj_mun_natureza ON mv_stats_natureza_juridica_municipio (cod_natureza);
CREATE INDEX idx_mv_nj_mun_estado ON mv_stats_natureza_juridica_municipio (cod_estado_ibge);
CREATE INDEX idx_mv_nj_mun_cidade ON mv_stats_natureza_juridica_municipio (cod_cidade_ibge);
CREATE INDEX idx_mv_nj_mun_total ON mv_stats_natureza_juridica_municipio (total DESC);
CREATE INDEX idx_mv_nj_mun_ativos ON mv_stats_natureza_juridica_municipio (ativos DESC);

-- Índice composto para consultas por estado + ordenação
CREATE INDEX idx_mv_nj_mun_estado_total ON mv_stats_natureza_juridica_municipio (cod_estado_ibge, total DESC);

-- Estatísticas para otimização do query planner
ANALYZE mv_stats_natureza_juridica_municipio;
