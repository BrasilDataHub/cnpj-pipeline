-- =============================================================================
-- mv_comparativo_natureza - Comparativo entre períodos equivalentes (natureza)
-- =============================================================================
-- Tempo estimado de criação: ~10 s (lê mv_movimentacao_mensal_natureza)
-- Periodicidade de refresh recomendada: junto de mv_movimentacao_mensal_natureza
-- Depende de: 00_helpers.sql, 15_mv_movimentacao_mensal_natureza.sql
-- =============================================================================
-- Mesmo contrato de mv_comparativo_territorio (ver aquele arquivo para a
-- convenção de janelas, a sentinela 0 e o viés de idade das coortes), com duas
-- diferenças:
--   - a entidade é `cod_natureza`, e a chave é (natureza x nível geográfico);
--   - não existe nível 'municipio': a série de origem para em UF.
--     `cod_cidade_ibge` existe para manter as três MVs de comparativo com o
--     mesmo shape, mas é SEMPRE 0.
--
--   brasil -> (0, 0, 0)   regiao -> (cod_regiao_ibge, 0, 0)   estado -> (0, cod_estado_ibge, 0)
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_comparativo_natureza CASCADE;

CREATE MATERIALIZED VIEW mv_comparativo_natureza AS
WITH ancora AS MATERIALIZED (
    SELECT fn_mes_ancora() AS mes_ancora
),
janelas AS (
    SELECT
        p.n::SMALLINT AS periodo_meses,
        a.mes_ancora,
        (a.mes_ancora - make_interval(months => p.n - 1))::DATE     AS periodo_atual_inicio,
        a.mes_ancora                                                AS periodo_atual_fim,
        (a.mes_ancora - make_interval(months => 2 * p.n - 1))::DATE AS periodo_anterior_inicio,
        (a.mes_ancora - make_interval(months => p.n))::DATE         AS periodo_anterior_fim
    FROM (VALUES (1), (6), (12), (24), (48)) AS p(n)
    CROSS JOIN ancora a
),
base AS (
    -- 95 meses = alcance do maior período (N=48).
    SELECT
        m.mes_abertura,
        m.cod_natureza,
        m.cod_regiao_ibge,
        m.cod_estado_ibge,
        m.aberturas,
        m.ainda_ativos,
        m.baixas
    FROM mv_movimentacao_mensal_natureza m
    CROSS JOIN ancora a
    WHERE m.mes_abertura >= (a.mes_ancora - INTERVAL '95 months')::DATE
      AND m.mes_abertura <= a.mes_ancora
),
niveis AS (
    SELECT
        b.cod_natureza,
        'brasil'::TEXT AS nivel,
        0 AS cod_regiao_ibge,
        0 AS cod_estado_ibge,
        b.mes_abertura,
        SUM(b.aberturas) AS aberturas,
        SUM(b.ainda_ativos) AS ainda_ativos,
        SUM(b.baixas) AS baixas
    FROM base b
    GROUP BY b.cod_natureza, b.mes_abertura

    UNION ALL

    SELECT
        b.cod_natureza,
        'regiao',
        b.cod_regiao_ibge,
        0,
        b.mes_abertura,
        SUM(b.aberturas),
        SUM(b.ainda_ativos),
        SUM(b.baixas)
    FROM base b
    WHERE b.cod_regiao_ibge IS NOT NULL
    GROUP BY b.cod_natureza, b.cod_regiao_ibge, b.mes_abertura

    UNION ALL

    SELECT
        b.cod_natureza,
        'estado',
        0,
        b.cod_estado_ibge,
        b.mes_abertura,
        SUM(b.aberturas),
        SUM(b.ainda_ativos),
        SUM(b.baixas)
    FROM base b
    WHERE b.cod_estado_ibge IS NOT NULL
    GROUP BY b.cod_natureza, b.cod_estado_ibge, b.mes_abertura
),
agregados AS (
    SELECT
        n.cod_natureza,
        n.nivel,
        n.cod_regiao_ibge,
        n.cod_estado_ibge,
        j.periodo_meses,
        j.mes_ancora,
        j.periodo_atual_inicio,
        j.periodo_atual_fim,
        j.periodo_anterior_inicio,
        j.periodo_anterior_fim,
        COALESCE(SUM(n.aberturas) FILTER (
            WHERE n.mes_abertura >= j.periodo_atual_inicio), 0)::BIGINT AS aberturas_atual,
        COALESCE(SUM(n.aberturas) FILTER (
            WHERE n.mes_abertura >= j.periodo_anterior_inicio
              AND n.mes_abertura <= j.periodo_anterior_fim), 0)::BIGINT AS aberturas_anterior,
        COALESCE(SUM(n.ainda_ativos) FILTER (
            WHERE n.mes_abertura >= j.periodo_atual_inicio), 0)::BIGINT AS ainda_ativos_atual,
        COALESCE(SUM(n.ainda_ativos) FILTER (
            WHERE n.mes_abertura >= j.periodo_anterior_inicio
              AND n.mes_abertura <= j.periodo_anterior_fim), 0)::BIGINT AS ainda_ativos_anterior,
        COALESCE(SUM(n.baixas) FILTER (
            WHERE n.mes_abertura >= j.periodo_atual_inicio), 0)::BIGINT AS baixas_atual,
        COALESCE(SUM(n.baixas) FILTER (
            WHERE n.mes_abertura >= j.periodo_anterior_inicio
              AND n.mes_abertura <= j.periodo_anterior_fim), 0)::BIGINT AS baixas_anterior
    FROM niveis n
    CROSS JOIN janelas j
    GROUP BY
        n.cod_natureza, n.nivel, n.cod_regiao_ibge, n.cod_estado_ibge,
        j.periodo_meses, j.mes_ancora,
        j.periodo_atual_inicio, j.periodo_atual_fim,
        j.periodo_anterior_inicio, j.periodo_anterior_fim
)
SELECT
    g.cod_natureza,
    g.nivel,
    g.cod_regiao_ibge,
    g.cod_estado_ibge,
    0 AS cod_cidade_ibge,
    g.periodo_meses,
    g.mes_ancora,
    g.periodo_atual_inicio,
    g.periodo_atual_fim,
    g.periodo_anterior_inicio,
    g.periodo_anterior_fim,

    -- Aberturas
    g.aberturas_atual,
    g.aberturas_anterior,
    (g.aberturas_atual - g.aberturas_anterior) AS aberturas_variacao_abs,
    fn_variacao_pct(g.aberturas_atual, g.aberturas_anterior)::NUMERIC(8, 2) AS aberturas_variacao_pct,

    -- Sobrevivência das coortes (ainda ativos / aberturas do próprio período)
    g.ainda_ativos_atual,
    g.ainda_ativos_anterior,
    fn_sobrevivencia_pct(g.ainda_ativos_atual, g.aberturas_atual)::NUMERIC(5, 2) AS sobrevivencia_atual_pct,
    fn_sobrevivencia_pct(g.ainda_ativos_anterior, g.aberturas_anterior)::NUMERIC(5, 2) AS sobrevivencia_anterior_pct,
    (fn_sobrevivencia_pct(g.ainda_ativos_atual, g.aberturas_atual)
     - fn_sobrevivencia_pct(g.ainda_ativos_anterior, g.aberturas_anterior))::NUMERIC(6, 2) AS sobrevivencia_delta_pp,

    -- Baixas
    g.baixas_atual,
    g.baixas_anterior,
    (g.baixas_atual - g.baixas_anterior) AS baixas_variacao_abs,
    fn_variacao_pct(g.baixas_atual, g.baixas_anterior)::NUMERIC(8, 2) AS baixas_variacao_pct,

    -- Saldo líquido
    (g.aberturas_atual - g.baixas_atual) AS saldo_atual,
    (g.aberturas_anterior - g.baixas_anterior) AS saldo_anterior
FROM agregados g;

-- Índices
-- cod_cidade_ibge fica fora do índice único por ser constante 0.
CREATE UNIQUE INDEX idx_mv_comp_natureza_pk
    ON mv_comparativo_natureza (cod_natureza, nivel, cod_regiao_ibge, cod_estado_ibge, periodo_meses);
CREATE INDEX idx_mv_comp_natureza_estado
    ON mv_comparativo_natureza (cod_estado_ibge, periodo_meses);
CREATE INDEX idx_mv_comp_natureza_regiao
    ON mv_comparativo_natureza (cod_regiao_ibge, periodo_meses);

COMMENT ON MATERIALIZED VIEW mv_comparativo_natureza IS
'Comparativo entre períodos equivalentes (1, 6, 12, 24 e 48 meses) por natureza
jurídica, nos níveis brasil/região/estado. Mesmo contrato de colunas de
mv_comparativo_territorio.';
COMMENT ON COLUMN mv_comparativo_natureza.nivel IS 'brasil | regiao | estado — define qual coluna de código geográfico está preenchida';
COMMENT ON COLUMN mv_comparativo_natureza.cod_cidade_ibge IS 'Sempre 0: este comparativo não desce a município (existe para uniformizar o shape das MVs de comparativo)';
COMMENT ON COLUMN mv_comparativo_natureza.periodo_meses IS 'Tamanho da janela em meses: 1, 6, 12, 24 ou 48';
COMMENT ON COLUMN mv_comparativo_natureza.aberturas_variacao_pct IS 'NULL quando o período anterior teve zero aberturas (o site mostra "Novo")';
COMMENT ON COLUMN mv_comparativo_natureza.sobrevivencia_delta_pp IS 'Diferença em pontos percentuais (atual - anterior); NULL se algum lado for NULL';

-- Estatísticas para otimização do query planner
ANALYZE mv_comparativo_natureza;
