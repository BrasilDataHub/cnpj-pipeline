-- =============================================================================
-- mv_abertura_periodo - Aberturas por mês/cidade (desde 2000)
-- =============================================================================
-- Tempo estimado de criação: ~10 min
-- Periodicidade de refresh recomendada: Semanal
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_abertura_periodo CASCADE;

CREATE MATERIALIZED VIEW mv_abertura_periodo AS
SELECT
    DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
    e.cod_cidade_ibge,
    e.cod_estado_ibge,
    COUNT(*) AS total_aberturas,
    COUNT(DISTINCT e.cnpj_basico) AS empresas_unicas,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos
FROM estabelecimento e
WHERE e.data_inicio_atividade IS NOT NULL
  AND e.data_inicio_atividade >= '2000-01-01'
GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), e.cod_cidade_ibge, e.cod_estado_ibge;

-- Índices
-- cod_estado_ibge entra no índice único para cobrir linhas com
-- cod_cidade_ibge NULL (estabelecimentos sem correspondência IBGE).
CREATE UNIQUE INDEX idx_mv_abertura_pk
    ON mv_abertura_periodo (mes_abertura, cod_cidade_ibge, cod_estado_ibge);
CREATE INDEX idx_mv_abertura_mes
    ON mv_abertura_periodo (mes_abertura);
CREATE INDEX idx_mv_abertura_estado
    ON mv_abertura_periodo (cod_estado_ibge);
CREATE INDEX idx_mv_abertura_periodo_cidade
    ON mv_abertura_periodo (cod_cidade_ibge, mes_abertura);

