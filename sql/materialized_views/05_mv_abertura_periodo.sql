-- =============================================================================
-- mv_abertura_periodo - Movimentação mensal por município (desde 2000)
-- =============================================================================
-- Tempo estimado de criação: ~15 min
-- Periodicidade de refresh recomendada: Semanal
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- Alimenta: 17_mv_comparativo_territorio.sql
-- =============================================================================
-- Mudança aditiva: o nome legado `total_aberturas` foi preservado (o site
-- consome esta MV pelo Model OpeningPeriod) e as colunas novas são
-- `cod_regiao_ibge`, `baixas`, `saldo`, `mes_ancora` e `mes_parcial`.
--
-- Duas agregações independentes reunidas por UNION ALL e re-agregadas:
--   - aberturas, pelo mês de `data_inicio_atividade`;
--   - baixas, pelo mês de `data_situacao_cadastral` das situações '08'.
-- Um mês pode ter baixa sem ter tido abertura (empresa antiga que fecha), e é
-- por isso que as duas pontas não podem ser um único GROUP BY: a chave de
-- agrupamento de cada uma é uma data diferente da mesma linha.
--
-- `mes_abertura` passa a significar "mês de movimentação" — mantido o nome por
-- compatibilidade com o Model e os índices existentes.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_abertura_periodo CASCADE;

CREATE MATERIALIZED VIEW mv_abertura_periodo AS
WITH ancora AS (
    SELECT fn_mes_ancora() AS mes_ancora
),
movimentacao AS (
    -- Aberturas: toda a coorte do mês, independentemente da situação atual.
    SELECT
        DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
        e.cod_cidade_ibge,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge) AS cod_regiao_ibge,
        COUNT(*) AS total_aberturas,
        COUNT(DISTINCT e.cnpj_basico) AS empresas_unicas,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos,
        0::BIGINT AS baixas
    FROM estabelecimento e
    WHERE e.data_inicio_atividade IS NOT NULL
      AND e.data_inicio_atividade >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), e.cod_cidade_ibge, e.cod_estado_ibge

    UNION ALL

    -- Baixas: situação cadastral '08' (baixada), pelo mês em que foi baixada.
    SELECT
        DATE_TRUNC('month', e.data_situacao_cadastral)::DATE,
        e.cod_cidade_ibge,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge),
        0::BIGINT,
        0::BIGINT,
        0::BIGINT,
        COUNT(*)
    FROM estabelecimento e
    WHERE e.cod_situacao_cadastral = '08'
      AND e.data_situacao_cadastral IS NOT NULL
      AND e.data_situacao_cadastral >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_situacao_cadastral), e.cod_cidade_ibge, e.cod_estado_ibge
)
SELECT
    m.mes_abertura,
    m.cod_cidade_ibge,
    m.cod_estado_ibge,
    MAX(m.cod_regiao_ibge) AS cod_regiao_ibge,
    SUM(m.total_aberturas)::BIGINT AS total_aberturas,
    SUM(m.empresas_unicas)::BIGINT AS empresas_unicas,
    SUM(m.ainda_ativos)::BIGINT AS ainda_ativos,
    SUM(m.baixas)::BIGINT AS baixas,
    (SUM(m.total_aberturas) - SUM(m.baixas))::BIGINT AS saldo,
    a.mes_ancora,
    (m.mes_abertura > a.mes_ancora) AS mes_parcial
FROM movimentacao m
CROSS JOIN ancora a
GROUP BY m.mes_abertura, m.cod_cidade_ibge, m.cod_estado_ibge, a.mes_ancora;

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
CREATE INDEX idx_mv_abertura_regiao
    ON mv_abertura_periodo (cod_regiao_ibge, mes_abertura);

COMMENT ON MATERIALIZED VIEW mv_abertura_periodo IS
'Movimentação mensal de empresas por município desde 2000-01: aberturas
(data_inicio_atividade), baixas (situação 08 por data_situacao_cadastral) e
saldo líquido. Granularidade municipal; agregados estaduais somam as cidades.';
COMMENT ON COLUMN mv_abertura_periodo.mes_abertura IS 'Mês da movimentação (nome legado; vale para aberturas e baixas)';
COMMENT ON COLUMN mv_abertura_periodo.total_aberturas IS 'Aberturas do mês, independentemente da situação cadastral atual';
COMMENT ON COLUMN mv_abertura_periodo.ainda_ativos IS 'Quantas daquelas aberturas seguem com situação 02 (base da sobrevivência)';
COMMENT ON COLUMN mv_abertura_periodo.baixas IS 'Estabelecimentos baixados (situação 08) no mês';
COMMENT ON COLUMN mv_abertura_periodo.saldo IS 'total_aberturas - baixas do mês';
COMMENT ON COLUMN mv_abertura_periodo.mes_ancora IS 'Último mês completo da carga (fn_mes_ancora); igual em todas as linhas';
COMMENT ON COLUMN mv_abertura_periodo.mes_parcial IS 'TRUE quando o mês é posterior à âncora, ou seja, ainda em consolidação';
COMMENT ON COLUMN mv_abertura_periodo.empresas_unicas IS
'COUNT(DISTINCT cnpj_basico) DENTRO do município. Somar cidades superconta
empresas com estabelecimentos em mais de um município; para deduplicação
exata em recortes maiores, consulte a tabela base.';

-- Estatísticas para otimização do query planner
ANALYZE mv_abertura_periodo;
