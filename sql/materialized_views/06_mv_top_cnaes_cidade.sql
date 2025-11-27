-- =============================================================================
-- mv_top_cnaes_cidade - Top 20 CNAEs por cidade
-- =============================================================================
-- Tempo estimado de criação: ~15 min
-- Periodicidade de refresh recomendada: Semanal
-- Nota: Esta view não possui índice único, então REFRESH CONCURRENTLY não é possível
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_top_cnaes_cidade CASCADE;

CREATE MATERIALIZED VIEW mv_top_cnaes_cidade AS
WITH ranked_cnaes AS (
    SELECT 
        e.cod_cidade_ibge,
        e.cod_cnae_principal,
        c.nome_cnae,
        COUNT(*) AS total,
        ROW_NUMBER() OVER (
            PARTITION BY e.cod_cidade_ibge 
            ORDER BY COUNT(*) DESC
        ) AS ranking
    FROM estabelecimento e
    JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
    WHERE e.cod_situacao_cadastral = '02'
    GROUP BY e.cod_cidade_ibge, e.cod_cnae_principal, c.nome_cnae
    HAVING COUNT(*) >= 10
)
SELECT 
    cod_cidade_ibge,
    cod_cnae_principal,
    nome_cnae,
    total,
    ranking
FROM ranked_cnaes
WHERE ranking <= 20;

-- Índices
CREATE INDEX idx_mv_top_cnaes_cidade 
    ON mv_top_cnaes_cidade (cod_cidade_ibge, ranking);
CREATE INDEX idx_mv_top_cnaes_cidade_cnae 
    ON mv_top_cnaes_cidade (cod_cnae_principal);

