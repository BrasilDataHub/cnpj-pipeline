-- =============================================================================
-- mv_movimentacao_mensal_porte - Série mensal por porte da empresa x UF
-- =============================================================================
-- Tempo estimado de criação: ~20 min
-- Periodicidade de refresh recomendada: Semanal
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- =============================================================================
-- Grão: mês x cod_porte x UF (~43k linhas). Códigos de porte: 00=Não informado,
-- 01=ME, 03=EPP, 05=Demais.
--
-- ATENÇÃO ao interpretar a série: `cod_porte` é o porte ATUAL da empresa, não o
-- porte que ela tinha quando abriu — a RFB não versiona esse campo. A série
-- responde "das empresas que hoje são ME, quantas abriram em cada mês", e não
-- "quantas ME foram abertas em cada mês". A diferença cresce quanto mais
-- antigo o mês.
--
-- Sem MV de comparativo: o viés acima torna a variação entre períodos
-- equivalentes enganosa (empresas migram de porte ao longo do tempo, o que
-- desloca a coorte inteira de faixa).
--
-- Mesmo padrão UNION ALL de 05_mv_abertura_periodo.sql.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_movimentacao_mensal_porte CASCADE;

CREATE MATERIALIZED VIEW mv_movimentacao_mensal_porte AS
WITH ancora AS (
    SELECT fn_mes_ancora() AS mes_ancora
),
movimentacao AS (
    -- Aberturas: toda a coorte do mês, independentemente da situação atual.
    SELECT
        DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
        emp.cod_porte,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge) AS cod_regiao_ibge,
        COUNT(*) AS aberturas,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos,
        0::BIGINT AS baixas
    FROM estabelecimento e
    JOIN empresa emp USING (cnpj_basico)
    WHERE e.data_inicio_atividade IS NOT NULL
      AND e.data_inicio_atividade >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), emp.cod_porte, e.cod_estado_ibge

    UNION ALL

    -- Baixas: situação cadastral '08' (baixada), pelo mês em que foi baixada.
    SELECT
        DATE_TRUNC('month', e.data_situacao_cadastral)::DATE,
        emp.cod_porte,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge),
        0::BIGINT,
        0::BIGINT,
        COUNT(*)
    FROM estabelecimento e
    JOIN empresa emp USING (cnpj_basico)
    WHERE e.cod_situacao_cadastral = '08'
      AND e.data_situacao_cadastral IS NOT NULL
      AND e.data_situacao_cadastral >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_situacao_cadastral), emp.cod_porte, e.cod_estado_ibge
)
SELECT
    m.mes_abertura,
    m.cod_porte,
    m.cod_estado_ibge,
    MAX(m.cod_regiao_ibge) AS cod_regiao_ibge,
    SUM(m.aberturas)::BIGINT AS aberturas,
    SUM(m.ainda_ativos)::BIGINT AS ainda_ativos,
    SUM(m.baixas)::BIGINT AS baixas,
    (SUM(m.aberturas) - SUM(m.baixas))::BIGINT AS saldo,
    a.mes_ancora,
    (m.mes_abertura > a.mes_ancora) AS mes_parcial
FROM movimentacao m
CROSS JOIN ancora a
GROUP BY m.mes_abertura, m.cod_porte, m.cod_estado_ibge, a.mes_ancora;

-- Índices
-- cod_porte e cod_estado_ibge são anuláveis; ambos entram no índice único pelo
-- mesmo critério de 05 (cobrir linhas sem correspondência).
CREATE UNIQUE INDEX idx_mv_mov_porte_pk
    ON mv_movimentacao_mensal_porte (mes_abertura, cod_porte, cod_estado_ibge);
CREATE INDEX idx_mv_mov_porte_porte
    ON mv_movimentacao_mensal_porte (cod_porte, mes_abertura);
CREATE INDEX idx_mv_mov_porte_estado
    ON mv_movimentacao_mensal_porte (cod_estado_ibge, mes_abertura);
CREATE INDEX idx_mv_mov_porte_regiao
    ON mv_movimentacao_mensal_porte (cod_regiao_ibge, mes_abertura);

COMMENT ON MATERIALIZED VIEW mv_movimentacao_mensal_porte IS
'Série mensal de aberturas, sobreviventes, baixas e saldo por porte da empresa
e UF, desde 2000-01. cod_porte é o porte ATUAL, não o da data de abertura —
ver o cabeçalho do SQL antes de usar em série histórica longa.';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.cod_porte IS 'Porte ATUAL da empresa (00=Não informado, 01=ME, 03=EPP, 05=Demais)';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.mes_abertura IS 'Mês da movimentação (aberturas e baixas)';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.aberturas IS 'Aberturas do mês, independentemente da situação cadastral atual';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.ainda_ativos IS 'Quantas daquelas aberturas seguem com situação 02 (base da sobrevivência)';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.baixas IS 'Estabelecimentos baixados (situação 08) no mês';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.saldo IS 'aberturas - baixas do mês';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.mes_ancora IS 'Último mês completo da carga (fn_mes_ancora); igual em todas as linhas';
COMMENT ON COLUMN mv_movimentacao_mensal_porte.mes_parcial IS 'TRUE quando o mês é posterior à âncora, ou seja, ainda em consolidação';

-- Estatísticas para otimização do query planner
ANALYZE mv_movimentacao_mensal_porte;
