-- =============================================================================
-- mv_movimentacao_mensal_cnae - Série mensal de movimentação por CNAE x UF
-- =============================================================================
-- Tempo estimado de criação: ~15 min
-- Periodicidade de refresh recomendada: Semanal
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- Alimenta: 18_mv_comparativo_cnae.sql
-- =============================================================================
-- Grão: mês x cod_cnae_principal x UF (~5-8M linhas). A região vem
-- desnormalizada (MAX de cod_regiao_ibge da UF) para permitir recortes
-- regionais somando UFs, sem JOIN.
--
-- Município fica de fora de propósito: o produto cartesiano
-- mês x CNAE x 5.570 municípios explodiria para centenas de milhões de linhas
-- para atender consultas que a página de CNAE x município resolve com as
-- janelas fixas de mv_top_cnaes_cidade.
--
-- Mesmo padrão UNION ALL de 05_mv_abertura_periodo.sql: aberturas pelo mês de
-- data_inicio_atividade, baixas ('08') pelo mês de data_situacao_cadastral.
-- Sem `empresas_unicas` — COUNT(DISTINCT cnpj_basico) por CNAE não é somável
-- entre CNAEs (a mesma empresa tem estabelecimentos com CNAEs diferentes) e
-- custaria um sort adicional sobre a tabela inteira.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_movimentacao_mensal_cnae CASCADE;

CREATE MATERIALIZED VIEW mv_movimentacao_mensal_cnae AS
WITH ancora AS (
    SELECT fn_mes_ancora() AS mes_ancora
),
movimentacao AS (
    -- Aberturas: toda a coorte do mês, independentemente da situação atual.
    SELECT
        DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
        e.cod_cnae_principal,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge) AS cod_regiao_ibge,
        COUNT(*) AS aberturas,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ainda_ativos,
        0::BIGINT AS baixas
    FROM estabelecimento e
    WHERE e.data_inicio_atividade IS NOT NULL
      AND e.data_inicio_atividade >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), e.cod_cnae_principal, e.cod_estado_ibge

    UNION ALL

    -- Baixas: situação cadastral '08' (baixada), pelo mês em que foi baixada.
    SELECT
        DATE_TRUNC('month', e.data_situacao_cadastral)::DATE,
        e.cod_cnae_principal,
        e.cod_estado_ibge,
        MAX(e.cod_regiao_ibge),
        0::BIGINT,
        0::BIGINT,
        COUNT(*)
    FROM estabelecimento e
    WHERE e.cod_situacao_cadastral = '08'
      AND e.data_situacao_cadastral IS NOT NULL
      AND e.data_situacao_cadastral >= DATE '2000-01-01'
    GROUP BY DATE_TRUNC('month', e.data_situacao_cadastral), e.cod_cnae_principal, e.cod_estado_ibge
)
SELECT
    m.mes_abertura,
    m.cod_cnae_principal,
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
GROUP BY m.mes_abertura, m.cod_cnae_principal, m.cod_estado_ibge, a.mes_ancora;

-- Índices
-- cod_estado_ibge entra no índice único para cobrir linhas com UF NULL
-- (estabelecimentos sem correspondência IBGE), mesmo critério de 05.
CREATE UNIQUE INDEX idx_mv_mov_cnae_pk
    ON mv_movimentacao_mensal_cnae (mes_abertura, cod_cnae_principal, cod_estado_ibge);
CREATE INDEX idx_mv_mov_cnae_cnae
    ON mv_movimentacao_mensal_cnae (cod_cnae_principal, mes_abertura);
CREATE INDEX idx_mv_mov_cnae_estado
    ON mv_movimentacao_mensal_cnae (cod_estado_ibge, mes_abertura);
CREATE INDEX idx_mv_mov_cnae_regiao
    ON mv_movimentacao_mensal_cnae (cod_regiao_ibge, mes_abertura);

COMMENT ON MATERIALIZED VIEW mv_movimentacao_mensal_cnae IS
'Série mensal de aberturas, sobreviventes, baixas e saldo por CNAE principal e
UF, desde 2000-01. Nível nacional = soma das UFs; regional = soma das UFs da
região. Não desce a município (ver cabeçalho do SQL).';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.mes_abertura IS 'Mês da movimentação (aberturas e baixas)';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.aberturas IS 'Aberturas do mês, independentemente da situação cadastral atual';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.ainda_ativos IS 'Quantas daquelas aberturas seguem com situação 02 (base da sobrevivência)';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.baixas IS 'Estabelecimentos baixados (situação 08) no mês';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.saldo IS 'aberturas - baixas do mês';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.mes_ancora IS 'Último mês completo da carga (fn_mes_ancora); igual em todas as linhas';
COMMENT ON COLUMN mv_movimentacao_mensal_cnae.mes_parcial IS 'TRUE quando o mês é posterior à âncora, ou seja, ainda em consolidação';

-- Estatísticas para otimização do query planner
ANALYZE mv_movimentacao_mensal_cnae;
