-- =============================================================================
-- mv_comparativo_territorio - Comparativo entre períodos equivalentes (território)
-- =============================================================================
-- Tempo estimado de criação: ~10 s (lê mv_abertura_periodo, não a tabela base)
-- Periodicidade de refresh recomendada: junto de mv_abertura_periodo
-- Depende de: 00_helpers.sql, 05_mv_abertura_periodo.sql
-- =============================================================================
-- Formato longo: 1 linha por território x periodo_meses (1, 6, 12, 24, 48).
-- O site lê os cinco períodos de uma entidade em UMA consulta — requisito do
-- banco remoto, onde cada round-trip custa ~350 ms.
--
-- CONVENÇÃO DE JANELAS (meses de calendário sobre mes_abertura, N = periodo_meses):
--   atual    = [ancora - (N-1) meses  ..  ancora]
--   anterior = [ancora - (2N-1) meses ..  ancora - N meses]
-- As colunas periodo_*_fim guardam o último mês INCLUSO, não o limite
-- exclusivo. Exemplo com âncora 2026-06 e N=6: atual 2026-01..2026-06,
-- anterior 2025-07..2025-12.
--
-- CHAVES: a coluna `nivel` diz qual recorte a linha representa e SOMENTE a
-- coluna geográfica daquele nível é preenchida; as demais recebem a sentinela
-- 0 (nunca NULL, que não indexa nem compara bem). Assim o site filtra
-- `nivel = 'estado' AND cod_estado_ibge = 35` sem ambiguidade e o índice único
-- cobre a chave inteira.
--   brasil    -> (0, 0, 0)
--   regiao    -> (cod_regiao_ibge, 0, 0)
--   estado    -> (0, cod_estado_ibge, 0)
--   municipio -> (0, 0, cod_cidade_ibge)
--
-- GRADE COMPLETA: todo território presente na base nos últimos 96 meses ganha
-- as 5 linhas de período, mesmo zeradas. Sem isso o site precisaria distinguir
-- "não houve abertura" de "a MV não tem essa linha" a cada render.
--
-- Linhas de mv_abertura_periodo com cod_cidade_ibge NULL (estabelecimento sem
-- correspondência IBGE) entram nos níveis brasil/região/estado, mas não geram
-- linha de município. Por isso a soma dos municípios de uma UF pode ficar
-- ligeiramente abaixo do total da própria UF.
--
-- O mês parcial (posterior à âncora) fica FORA dos comparativos: comparar um
-- mês incompleto com um mês fechado produziria queda artificial em todo lugar.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_comparativo_territorio CASCADE;

CREATE MATERIALIZED VIEW mv_comparativo_territorio AS
WITH ancora AS (
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
    -- 95 meses é exatamente o alcance do maior período (N=48: anterior começa
    -- em ancora - 95 meses). Ler os 26 anos inteiros da série seria desperdício.
    SELECT
        m.mes_abertura,
        m.cod_regiao_ibge,
        m.cod_estado_ibge,
        m.cod_cidade_ibge,
        m.total_aberturas AS aberturas,
        m.ainda_ativos,
        m.baixas
    FROM mv_abertura_periodo m
    CROSS JOIN ancora a
    WHERE m.mes_abertura >= (a.mes_ancora - INTERVAL '95 months')::DATE
      AND m.mes_abertura <= a.mes_ancora
),
niveis AS (
    SELECT
        'brasil'::TEXT AS nivel,
        0 AS cod_regiao_ibge,
        0 AS cod_estado_ibge,
        0 AS cod_cidade_ibge,
        b.mes_abertura,
        SUM(b.aberturas) AS aberturas,
        SUM(b.ainda_ativos) AS ainda_ativos,
        SUM(b.baixas) AS baixas
    FROM base b
    GROUP BY b.mes_abertura

    UNION ALL

    SELECT
        'regiao',
        b.cod_regiao_ibge,
        0,
        0,
        b.mes_abertura,
        SUM(b.aberturas),
        SUM(b.ainda_ativos),
        SUM(b.baixas)
    FROM base b
    WHERE b.cod_regiao_ibge IS NOT NULL
    GROUP BY b.cod_regiao_ibge, b.mes_abertura

    UNION ALL

    SELECT
        'estado',
        0,
        b.cod_estado_ibge,
        0,
        b.mes_abertura,
        SUM(b.aberturas),
        SUM(b.ainda_ativos),
        SUM(b.baixas)
    FROM base b
    WHERE b.cod_estado_ibge IS NOT NULL
    GROUP BY b.cod_estado_ibge, b.mes_abertura

    UNION ALL

    SELECT
        'municipio',
        0,
        0,
        b.cod_cidade_ibge,
        b.mes_abertura,
        SUM(b.aberturas),
        SUM(b.ainda_ativos),
        SUM(b.baixas)
    FROM base b
    WHERE b.cod_cidade_ibge IS NOT NULL
    GROUP BY b.cod_cidade_ibge, b.mes_abertura
),
agregados AS (
    SELECT
        n.nivel,
        n.cod_regiao_ibge,
        n.cod_estado_ibge,
        n.cod_cidade_ibge,
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
        n.nivel, n.cod_regiao_ibge, n.cod_estado_ibge, n.cod_cidade_ibge,
        j.periodo_meses, j.mes_ancora,
        j.periodo_atual_inicio, j.periodo_atual_fim,
        j.periodo_anterior_inicio, j.periodo_anterior_fim
)
SELECT
    g.nivel,
    g.cod_regiao_ibge,
    g.cod_estado_ibge,
    g.cod_cidade_ibge,
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
-- A sentinela 0 (em vez de NULL) nas colunas não aplicáveis é o que permite um
-- índice único simples cobrindo a chave inteira.
CREATE UNIQUE INDEX idx_mv_comp_territorio_pk
    ON mv_comparativo_territorio (nivel, cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge, periodo_meses);
CREATE INDEX idx_mv_comp_territorio_estado
    ON mv_comparativo_territorio (cod_estado_ibge, periodo_meses);
CREATE INDEX idx_mv_comp_territorio_cidade
    ON mv_comparativo_territorio (cod_cidade_ibge, periodo_meses);
CREATE INDEX idx_mv_comp_territorio_regiao
    ON mv_comparativo_territorio (cod_regiao_ibge, periodo_meses);

COMMENT ON MATERIALIZED VIEW mv_comparativo_territorio IS
'Comparativo entre períodos equivalentes (1, 6, 12, 24 e 48 meses) por
território, ancorado no último mês completo da carga. Formato longo: 1 linha
por (nivel, código do território, periodo_meses).';
COMMENT ON COLUMN mv_comparativo_territorio.nivel IS 'brasil | regiao | estado | municipio — define qual coluna de código está preenchida';
COMMENT ON COLUMN mv_comparativo_territorio.periodo_meses IS 'Tamanho da janela em meses: 1, 6, 12, 24 ou 48';
COMMENT ON COLUMN mv_comparativo_territorio.mes_ancora IS 'Último mês completo da carga (fn_mes_ancora); igual em todas as linhas';
COMMENT ON COLUMN mv_comparativo_territorio.periodo_atual_fim IS 'Último mês INCLUSO no período atual (= mes_ancora)';
COMMENT ON COLUMN mv_comparativo_territorio.periodo_anterior_fim IS 'Último mês INCLUSO no período anterior';
COMMENT ON COLUMN mv_comparativo_territorio.aberturas_variacao_pct IS 'NULL quando o período anterior teve zero aberturas (o site mostra "Novo")';
COMMENT ON COLUMN mv_comparativo_territorio.sobrevivencia_atual_pct IS
'ainda_ativos_atual / aberturas_atual. NULL quando não houve aberturas.
A coorte atual é mais jovem que a anterior e teve menos tempo para fechar:
o delta é indicador de tendência, não medida de qualidade.';
COMMENT ON COLUMN mv_comparativo_territorio.sobrevivencia_delta_pp IS 'Diferença em pontos percentuais (atual - anterior); NULL se algum lado for NULL';

-- Estatísticas para otimização do query planner
ANALYZE mv_comparativo_territorio;
