-- ============================================================================
-- Materialized View: mv_stats_municipio (v2)
-- Descrição: Estatísticas de empresas por município com métricas apenas de ativas
-- ============================================================================
-- IMPORTANTE: Execute este script no banco companies_pgsql
-- ============================================================================

-- Remover view antiga se existir
DROP MATERIALIZED VIEW IF EXISTS mv_stats_municipio CASCADE;

-- Criar nova view com métricas corrigidas
CREATE MATERIALIZED VIEW mv_stats_municipio AS
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

    -- Empresas ativas abertas nos últimos 6 meses
    count(*) FILTER (
        WHERE e.cod_situacao_cadastral = '02'
        AND e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months'
    ) AS novos_6meses,

    -- Empresas ativas abertas no último ano
    count(*) FILTER (
        WHERE e.cod_situacao_cadastral = '02'
        AND e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year'
    ) AS novos_1ano

FROM estabelecimento e
INNER JOIN ibge_cidade mun ON e.cod_cidade_ibge = mun.cod_cidade_ibge
INNER JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY
    e.cod_cidade_ibge,
    mun.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    est.cod_regiao_ibge
ORDER BY est.sigla_uf, mun.nome_cidade;

-- Criar índice único para refresh concorrente
CREATE UNIQUE INDEX idx_mv_stats_municipio_pk ON mv_stats_municipio (cod_cidade_ibge);

-- Índices adicionais para consultas frequentes
CREATE INDEX idx_mv_stats_municipio_estado ON mv_stats_municipio (cod_estado_ibge);
CREATE INDEX idx_mv_stats_municipio_regiao ON mv_stats_municipio (cod_regiao_ibge);
CREATE INDEX idx_mv_stats_municipio_sigla ON mv_stats_municipio (sigla_uf);

-- Comentários
COMMENT ON MATERIALIZED VIEW mv_stats_municipio IS 'Estatísticas de empresas agregadas por município - v2 com métricas de empresas ativas';
COMMENT ON COLUMN mv_stats_municipio.ativos IS 'Total de empresas com situação cadastral ativa (02)';
COMMENT ON COLUMN mv_stats_municipio.matrizes_ativas IS 'Total de matrizes ativas (situação 02 e matriz_filial 1)';
COMMENT ON COLUMN mv_stats_municipio.filiais_ativas IS 'Total de filiais ativas (situação 02 e matriz_filial 2)';
COMMENT ON COLUMN mv_stats_municipio.novos_6meses IS 'Empresas ativas abertas nos últimos 6 meses';
COMMENT ON COLUMN mv_stats_municipio.novos_1ano IS 'Empresas ativas abertas no último ano';

-- ============================================================================
-- Para atualizar a view posteriormente:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_municipio;
-- ============================================================================
