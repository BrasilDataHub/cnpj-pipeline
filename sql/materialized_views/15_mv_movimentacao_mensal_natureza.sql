-- =============================================================================
-- mv_movimentacao_mensal_natureza - Série mensal por natureza jurídica x UF
-- =============================================================================
-- Tempo estimado de criação: ~20 min
-- Periodicidade de refresh recomendada: Semanal
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- Alimenta: 19_mv_comparativo_natureza.sql
-- =============================================================================
-- Grão: mês x cod_natureza x UF (~500k linhas). A natureza jurídica vive em
-- `empresa`, então cada braço faz o JOIN por cnpj_basico; a coluna é exposta
-- como `cod_natureza` (e não `cod_natureza_juridica`) para casar com as demais
-- MVs de natureza (mv_stats_natureza_juridica*, mv_stats_natureza_juridica_cnae).
--
-- A contagem é de ESTABELECIMENTOS, não de empresas: um CNPJ básico com cinco
-- filiais conta cinco aberturas, igual às demais séries. Isso mantém as séries
-- somáveis entre si; as MVs de estoque por natureza (10/11/12) é que contam
-- COUNT(DISTINCT cnpj_basico) de matrizes.
--
-- Mesmo padrão UNION ALL de 05_mv_abertura_periodo.sql.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_movimentacao_mensal_natureza CASCADE;

CREATE MATERIALIZED VIEW mv_movimentacao_mensal_natureza AS
WITH ancora AS MATERIALIZED (
    SELECT fn_mes_ancora() AS mes_ancora
),
movimentacao AS (
    -- Aberturas: toda a coorte do mês, independentemente da situação atual.
    SELECT
        DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
        emp.cod_natureza_juridica AS cod_natureza,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge) AS cod_regiao_ibge,
        COUNT(*) AS aberturas,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos,
        0::BIGINT AS baixas
    FROM estabelecimento e
    JOIN empresa emp USING (cnpj_basico)
    WHERE e.data_inicio_atividade IS NOT NULL
      AND e.data_inicio_atividade >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), emp.cod_natureza_juridica, e.cod_estado_ibge

    UNION ALL

    -- Baixas: situação cadastral '08' (baixada), pelo mês em que foi baixada.
    SELECT
        DATE_TRUNC('month', e.data_situacao_cadastral)::DATE,
        emp.cod_natureza_juridica,
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
    GROUP BY DATE_TRUNC('month', e.data_situacao_cadastral), emp.cod_natureza_juridica, e.cod_estado_ibge
)
SELECT
    m.mes_abertura,
    m.cod_natureza,
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
GROUP BY m.mes_abertura, m.cod_natureza, m.cod_estado_ibge, a.mes_ancora;

-- Índices
-- cod_estado_ibge entra no índice único para cobrir linhas com UF NULL
-- (estabelecimentos sem correspondência IBGE), mesmo critério de 05.
CREATE UNIQUE INDEX idx_mv_mov_natureza_pk
    ON mv_movimentacao_mensal_natureza (mes_abertura, cod_natureza, cod_estado_ibge);
CREATE INDEX idx_mv_mov_natureza_natureza
    ON mv_movimentacao_mensal_natureza (cod_natureza, mes_abertura);
CREATE INDEX idx_mv_mov_natureza_estado
    ON mv_movimentacao_mensal_natureza (cod_estado_ibge, mes_abertura);
CREATE INDEX idx_mv_mov_natureza_regiao
    ON mv_movimentacao_mensal_natureza (cod_regiao_ibge, mes_abertura);

COMMENT ON MATERIALIZED VIEW mv_movimentacao_mensal_natureza IS
'Série mensal de aberturas, sobreviventes, baixas e saldo por natureza jurídica
e UF, desde 2000-01. Conta estabelecimentos (não CNPJs básicos distintos).';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.mes_abertura IS 'Mês da movimentação (aberturas e baixas)';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.aberturas IS 'Aberturas do mês, independentemente da situação cadastral atual';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.ainda_ativos IS 'Quantas daquelas aberturas seguem com situação 02 (base da sobrevivência)';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.baixas IS 'Estabelecimentos baixados (situação 08) no mês';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.saldo IS 'aberturas - baixas do mês';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.mes_ancora IS 'Último mês completo da carga (fn_mes_ancora); igual em todas as linhas';
COMMENT ON COLUMN mv_movimentacao_mensal_natureza.mes_parcial IS 'TRUE quando o mês é posterior à âncora, ou seja, ainda em consolidação';

-- Estatísticas para otimização do query planner
ANALYZE mv_movimentacao_mensal_natureza;
