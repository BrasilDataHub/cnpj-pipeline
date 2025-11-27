-- =============================================================================
-- mv_regime_tributario_cidade - Regime Tributário por Cidade
-- =============================================================================
-- Objetivo: Otimizar consultas que filtram por Simples Nacional / MEI
-- Resolve: JOINs custosos entre estabelecimento e simples + agregações
-- Tempo estimado de criação: ~15 min (requer JOIN com simples)
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_regime_tributario_cidade CASCADE;

CREATE MATERIALIZED VIEW mv_regime_tributario_cidade AS
SELECT 
    e.cod_cidade_ibge,
    e.cod_estado_ibge,
    e.cod_regiao_ibge,
    e.cod_situacao_cadastral,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE s.opcao_simples = 'S') AS simples_nacional,
    COUNT(*) FILTER (WHERE s.opcao_mei = 'S') AS mei,
    COUNT(*) FILTER (WHERE s.opcao_simples = 'S' OR s.opcao_mei = 'S') AS simples_ou_mei,
    COUNT(*) FILTER (WHERE COALESCE(s.opcao_simples, 'N') != 'S' AND COALESCE(s.opcao_mei, 'N') != 'S') AS lucro_presumido_real
FROM estabelecimento e
LEFT JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
LEFT JOIN simples s ON emp.cnpj_basico = s.cnpj_basico
GROUP BY e.cod_cidade_ibge, e.cod_estado_ibge, e.cod_regiao_ibge, e.cod_situacao_cadastral;

-- Índice único para REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_regime_tributario_pk 
    ON mv_regime_tributario_cidade (cod_cidade_ibge, cod_situacao_cadastral);

-- Índices auxiliares para consultas frequentes
CREATE INDEX idx_mv_regime_tributario_estado 
    ON mv_regime_tributario_cidade (cod_estado_ibge, cod_situacao_cadastral);
CREATE INDEX idx_mv_regime_tributario_regiao 
    ON mv_regime_tributario_cidade (cod_regiao_ibge, cod_situacao_cadastral);

COMMENT ON MATERIALIZED VIEW mv_regime_tributario_cidade IS 
'Contagem de empresas por regime tributário (Simples/MEI) por cidade.
Uso principal: Filtros avançados de regime tributário na busca de empresas.
Evita JOINs custosos entre estabelecimento → empresa → simples (~68M + 45M registros).';

