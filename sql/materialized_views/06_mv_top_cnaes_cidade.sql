-- Atualizacao da View Materializada: mv_top_cnaes_cidade
-- Adiciona colunas para analise de tendencias de setores economicos em nivel municipal
--
-- IMPORTANTE: Execute este script para atualizar a view existente
-- A view sera recriada mantendo os campos existentes e adicionando novos campos

-- 1. Remover a view existente (indices serao removidos automaticamente)
DROP MATERIALIZED VIEW IF EXISTS mv_top_cnaes_cidade;

-- 2. Criar a view atualizada com as novas colunas
CREATE MATERIALIZED VIEW mv_top_cnaes_cidade AS
WITH cnae_cidade_stats AS (
    SELECT
        e.cod_cidade_ibge,
        e.cod_cnae_principal,
        c.nome_cnae,
        mun.cod_estado_ibge,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02'
            AND e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
        COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02'
            AND e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano
    FROM estabelecimento e
    JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
    JOIN ibge_cidade mun ON e.cod_cidade_ibge = mun.cod_cidade_ibge
    GROUP BY e.cod_cidade_ibge, e.cod_cnae_principal, c.nome_cnae, mun.cod_estado_ibge
    HAVING COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') >= 5
)
SELECT
    cod_cidade_ibge,
    cod_cnae_principal,
    nome_cnae,
    cod_estado_ibge,
    total,
    ativos,
    novos_6meses,
    novos_1ano,
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

-- Comando para atualizar a view materializada (executar periodicamente)
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_top_cnaes_cidade;
