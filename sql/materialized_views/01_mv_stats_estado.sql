-- ============================================================================
-- Materialized View: mv_stats_estado (v2)
-- Descrição: Estatísticas de empresas por estado com métricas apenas de ativas
-- ============================================================================
-- IMPORTANTE: Execute este script no banco companies_pgsql
-- ============================================================================

-- Remover view antiga se existir
DROP MATERIALIZED VIEW IF EXISTS mv_stats_estado CASCADE;

-- Criar nova view com métricas corrigidas
CREATE MATERIALIZED VIEW mv_stats_estado AS
SELECT
    e.cod_estado_ibge,
    est.sigla_uf,
    est.nome_estado,
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
INNER JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY
    e.cod_estado_ibge,
    est.sigla_uf,
    est.nome_estado,
    est.cod_regiao_ibge
ORDER BY est.sigla_uf;

-- Criar índice único para refresh concorrente
CREATE UNIQUE INDEX idx_mv_stats_estado_pk ON mv_stats_estado (cod_estado_ibge);

-- Índices adicionais para consultas frequentes
CREATE INDEX idx_mv_stats_estado_regiao ON mv_stats_estado (cod_regiao_ibge);
CREATE INDEX idx_mv_stats_estado_sigla ON mv_stats_estado (sigla_uf);

-- Comentários
COMMENT ON MATERIALIZED VIEW mv_stats_estado IS 'Estatísticas de empresas agregadas por estado - v2 com métricas de empresas ativas';
COMMENT ON COLUMN mv_stats_estado.ativos IS 'Total de empresas com situação cadastral ativa (02)';
COMMENT ON COLUMN mv_stats_estado.matrizes_ativas IS 'Total de matrizes ativas (situação 02 e matriz_filial 1)';
COMMENT ON COLUMN mv_stats_estado.filiais_ativas IS 'Total de filiais ativas (situação 02 e matriz_filial 2)';
COMMENT ON COLUMN mv_stats_estado.novos_6meses IS 'Empresas ativas abertas nos últimos 6 meses';
COMMENT ON COLUMN mv_stats_estado.novos_1ano IS 'Empresas ativas abertas no último ano';

-- ============================================================================
-- Para atualizar a view posteriormente:
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_estado;
-- ============================================================================
