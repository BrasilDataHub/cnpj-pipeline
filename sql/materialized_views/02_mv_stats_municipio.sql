-- ============================================================================
-- Materialized View: mv_stats_municipio (v3)
-- Descrição: Estatísticas de empresas por município com métricas apenas de ativas
-- ============================================================================
-- IMPORTANTE: Execute este script no banco companies_pgsql
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- ============================================================================
-- MUDANÇA DE SEMÂNTICA (v3) — as colunas `novos_*` mudaram de valor: janelas
-- ancoradas em fn_mes_ancora() no lugar de CURRENT_DATE e sem o filtro de
-- situação cadastral. Ver o cabeçalho de 01_mv_stats_estado.sql.
-- ============================================================================

-- Remover view antiga se existir
DROP MATERIALIZED VIEW IF EXISTS mv_stats_municipio CASCADE;

-- Criar nova view com métricas corrigidas
CREATE MATERIALIZED VIEW mv_stats_municipio AS
WITH janelas AS MATERIALIZED (
    SELECT a.mes_ancora,
           a.mes_ancora                                  AS ini_1mes,
           (a.mes_ancora - INTERVAL '5 months')::DATE    AS ini_6meses,
           (a.mes_ancora - INTERVAL '11 months')::DATE   AS ini_1ano,
           (a.mes_ancora - INTERVAL '23 months')::DATE   AS ini_2anos,
           (a.mes_ancora - INTERVAL '47 months')::DATE   AS ini_4anos,
           (a.mes_ancora + INTERVAL '1 month')::DATE     AS fim_exclusivo
      FROM (SELECT fn_mes_ancora() AS mes_ancora) a
)
SELECT
    e.cod_cidade_ibge,
    mun.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    est.cod_regiao_ibge,

    -- Total de estabelecimentos (todos os registros)
    count(*) AS total_estabelecimentos,

    -- Total de empresas ativas (situação cadastral = '02')
    count(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,

    -- Matrizes ativas (matriz_filial = '1' e situação = '02')
    count(*) FILTER (WHERE e.cod_situacao_cadastral = '02' AND e.matriz_filial = '1') AS matrizes_ativas,

    -- Filiais ativas (matriz_filial = '2' e situação = '02')
    count(*) FILTER (WHERE e.cod_situacao_cadastral = '02' AND e.matriz_filial = '2') AS filiais_ativas,

    -- Total de empresas (CNPJs base únicos)
    count(DISTINCT e.cnpj_basico) AS total_empresas,

    -- Data da primeira e última abertura
    min(e.data_inicio_atividade) AS primeira_abertura,
    max(e.data_inicio_atividade) AS ultima_abertura,

    -- Aberturas no último mês completo da carga
    count(*) FILTER (
        WHERE e.data_inicio_atividade >= j.ini_1mes
        AND e.data_inicio_atividade < j.fim_exclusivo
    ) AS novos_1mes,

    -- Aberturas nos últimos 6 meses (janela ancorada, fim inclusivo na âncora)
    count(*) FILTER (
        WHERE e.data_inicio_atividade >= j.ini_6meses
        AND e.data_inicio_atividade < j.fim_exclusivo
    ) AS novos_6meses,

    -- Aberturas nos últimos 12 meses
    count(*) FILTER (
        WHERE e.data_inicio_atividade >= j.ini_1ano
        AND e.data_inicio_atividade < j.fim_exclusivo
    ) AS novos_1ano,

    -- Aberturas nos últimos 24 meses
    count(*) FILTER (
        WHERE e.data_inicio_atividade >= j.ini_2anos
        AND e.data_inicio_atividade < j.fim_exclusivo
    ) AS novos_2anos,

    -- Aberturas nos últimos 48 meses
    count(*) FILTER (
        WHERE e.data_inicio_atividade >= j.ini_4anos
        AND e.data_inicio_atividade < j.fim_exclusivo
    ) AS novos_4anos,

    -- Âncora usada nas janelas acima (auditoria e rótulo no site)
    j.mes_ancora

FROM estabelecimento e
INNER JOIN ibge_cidade mun ON e.cod_cidade_ibge = mun.cod_cidade_ibge
INNER JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
CROSS JOIN janelas j
GROUP BY
    e.cod_cidade_ibge,
    mun.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    est.cod_regiao_ibge,
    j.mes_ancora
ORDER BY est.sigla_uf, mun.nome_cidade;

-- Criar índice único para refresh concorrente
CREATE UNIQUE INDEX idx_mv_stats_municipio_pk ON mv_stats_municipio (cod_cidade_ibge);

-- Índices adicionais para consultas frequentes
CREATE INDEX idx_mv_stats_municipio_estado ON mv_stats_municipio (cod_estado_ibge);
CREATE INDEX idx_mv_stats_municipio_regiao ON mv_stats_municipio (cod_regiao_ibge);
CREATE INDEX idx_mv_stats_municipio_sigla ON mv_stats_municipio (sigla_uf);

-- Comentários
COMMENT ON MATERIALIZED VIEW mv_stats_municipio IS 'Estatísticas de empresas agregadas por município - v3 com janelas ancoradas em fn_mes_ancora()';
COMMENT ON COLUMN mv_stats_municipio.ativos IS 'Total de empresas com situação cadastral ativa (02)';
COMMENT ON COLUMN mv_stats_municipio.matrizes_ativas IS 'Total de matrizes ativas (situação 02 e matriz_filial 1)';
COMMENT ON COLUMN mv_stats_municipio.filiais_ativas IS 'Total de filiais ativas (situação 02 e matriz_filial 2)';
COMMENT ON COLUMN mv_stats_municipio.novos_1mes IS 'Aberturas no mês da âncora (último mês completo da carga)';
COMMENT ON COLUMN mv_stats_municipio.novos_6meses IS 'Aberturas nos 6 meses terminados na âncora (inclusive)';
COMMENT ON COLUMN mv_stats_municipio.novos_1ano IS 'Aberturas nos 12 meses terminados na âncora (inclusive)';
COMMENT ON COLUMN mv_stats_municipio.novos_2anos IS 'Aberturas nos 24 meses terminados na âncora (inclusive)';
COMMENT ON COLUMN mv_stats_municipio.novos_4anos IS 'Aberturas nos 48 meses terminados na âncora (inclusive)';
COMMENT ON COLUMN mv_stats_municipio.mes_ancora IS 'Último mês completo da carga usado nas janelas novos_* (fn_mes_ancora)';

-- ============================================================================
-- Para atualizar a view posteriormente:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_municipio;
-- ============================================================================
