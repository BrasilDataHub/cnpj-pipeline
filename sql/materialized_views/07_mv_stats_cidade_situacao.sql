-- =============================================================================
-- mv_stats_cidade_situacao - Estatísticas por Cidade e Situação Cadastral
-- =============================================================================
-- Objetivo: Fornecer contagens rápidas segmentadas por situação cadastral
-- Resolve: Consultas de contagem que precisavam fazer GROUP BY na tabela grande
-- Tempo estimado de criação: ~8 min
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_cidade_situacao CASCADE;

CREATE MATERIALIZED VIEW mv_stats_cidade_situacao AS
SELECT 
    e.cod_cidade_ibge,
    e.cod_situacao_cadastral,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '' AND e.email NOT ILIKE '%contab%') AS email_prospeccao
FROM estabelecimento e
GROUP BY e.cod_cidade_ibge, e.cod_situacao_cadastral;

-- Índice único para REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_stats_cidade_situacao_pk 
    ON mv_stats_cidade_situacao (cod_cidade_ibge, cod_situacao_cadastral);

-- Índices auxiliares
CREATE INDEX idx_mv_stats_cidade_situacao_cidade 
    ON mv_stats_cidade_situacao (cod_cidade_ibge);
CREATE INDEX idx_mv_stats_cidade_situacao_situacao 
    ON mv_stats_cidade_situacao (cod_situacao_cadastral);

COMMENT ON MATERIALIZED VIEW mv_stats_cidade_situacao IS 
'Estatísticas agregadas por cidade e situação cadastral.
Uso principal: Contagens rápidas para filtros de listagem de empresas.
Evita GROUP BY na tabela estabelecimento (~68M registros).';

