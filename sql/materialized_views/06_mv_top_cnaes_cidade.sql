-- Atualizacao da View Materializada: mv_top_cnaes_cidade
-- Adiciona colunas para analise de tendencias de setores economicos em nivel municipal
--
-- IMPORTANTE: Execute este script para atualizar a view existente
-- A view sera recriada mantendo os campos existentes e adicionando novos campos
-- Depende de: 00_helpers.sql (fn_mes_ancora)
--
-- MUDANCA DE SEMANTICA — as colunas `novos_*` mudaram de valor: janelas
-- ancoradas em fn_mes_ancora() no lugar de CURRENT_DATE e sem o filtro de
-- situacao cadastral. Ver o cabecalho de 01_mv_stats_estado.sql.

-- 1. Remover a view existente (indices serao removidos automaticamente)
DROP MATERIALIZED VIEW IF EXISTS mv_top_cnaes_cidade CASCADE;

-- 2. Criar a view atualizada com as novas colunas
CREATE MATERIALIZED VIEW mv_top_cnaes_cidade AS
WITH janela AS MATERIALIZED (
    SELECT a.mes_ancora,
           a.mes_ancora                                  AS ini_1mes,
           (a.mes_ancora - INTERVAL '5 months')::DATE    AS ini_6meses,
           (a.mes_ancora - INTERVAL '11 months')::DATE   AS ini_1ano,
           (a.mes_ancora + INTERVAL '1 month')::DATE     AS fim_exclusivo
      FROM (SELECT fn_mes_ancora() AS mes_ancora) a
),
cnae_cidade_stats AS (
    SELECT
        e.cod_cidade_ibge,
        e.cod_cnae_principal,
        c.nome_cnae,
        mun.cod_estado_ibge,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
        COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_1mes
            AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_1mes,
        COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_6meses
            AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_6meses,
        COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_1ano
            AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_1ano,
        j.mes_ancora
    FROM estabelecimento e
    JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
    JOIN ibge_cidade mun ON e.cod_cidade_ibge = mun.cod_cidade_ibge
    CROSS JOIN janela j
    GROUP BY e.cod_cidade_ibge, e.cod_cnae_principal, c.nome_cnae, mun.cod_estado_ibge, j.mes_ancora
    HAVING COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') >= 5
)
SELECT
    cod_cidade_ibge,
    cod_cnae_principal,
    nome_cnae,
    cod_estado_ibge,
    total,
    ativos,
    novos_1mes,
    novos_6meses,
    novos_1ano,
    mes_ancora,
    ROW_NUMBER() OVER (
        PARTITION BY cod_cidade_ibge
        ORDER BY ativos DESC
    ) AS ranking
FROM cnae_cidade_stats;

-- 3. Recriar indices para otimizar consultas
CREATE UNIQUE INDEX idx_mv_top_cnaes_cidade_pk ON mv_top_cnaes_cidade(cod_cidade_ibge, cod_cnae_principal);
CREATE INDEX idx_mv_top_cnaes_cidade_cidade ON mv_top_cnaes_cidade(cod_cidade_ibge);
CREATE INDEX idx_mv_top_cnaes_cidade_cnae ON mv_top_cnaes_cidade(cod_cnae_principal);
CREATE INDEX idx_mv_top_cnaes_cidade_estado ON mv_top_cnaes_cidade(cod_estado_ibge);
CREATE INDEX idx_mv_top_cnaes_cidade_ranking ON mv_top_cnaes_cidade(cod_cidade_ibge, ranking);
CREATE INDEX idx_mv_top_cnaes_cidade_ativos ON mv_top_cnaes_cidade(ativos DESC);

COMMENT ON COLUMN mv_top_cnaes_cidade.novos_1mes IS 'Aberturas no mes da ancora (ultimo mes completo da carga)';
COMMENT ON COLUMN mv_top_cnaes_cidade.mes_ancora IS 'Ultimo mes completo da carga usado nas janelas novos_* (fn_mes_ancora)';

-- Comando para atualizar a view materializada (executar periodicamente)
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_top_cnaes_cidade;
