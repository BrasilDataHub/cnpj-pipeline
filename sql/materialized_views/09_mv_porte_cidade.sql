-- =============================================================================
-- mv_porte_cidade - Porte da Empresa por Cidade
-- =============================================================================
-- Objetivo: Otimizar consultas que filtram por porte da empresa
-- Resolve: JOINs custosos entre estabelecimento e empresa para obter porte
-- Tempo estimado de criação: ~12 min
-- Periodicidade de refresh recomendada: Diário
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_porte_cidade CASCADE;

CREATE MATERIALIZED VIEW mv_porte_cidade AS
SELECT 
    e.cod_cidade_ibge,
    e.cod_estado_ibge,
    e.cod_regiao_ibge,
    emp.cod_porte,
    e.cod_situacao_cadastral,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email
FROM estabelecimento e
JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
GROUP BY e.cod_cidade_ibge, e.cod_estado_ibge, e.cod_regiao_ibge, emp.cod_porte, e.cod_situacao_cadastral;

-- Índice único para REFRESH CONCURRENTLY
CREATE UNIQUE INDEX idx_mv_porte_cidade_pk 
    ON mv_porte_cidade (cod_cidade_ibge, cod_porte, cod_situacao_cadastral);

-- Índices auxiliares para consultas por estado/região
CREATE INDEX idx_mv_porte_cidade_estado 
    ON mv_porte_cidade (cod_estado_ibge, cod_porte, cod_situacao_cadastral);
CREATE INDEX idx_mv_porte_cidade_porte 
    ON mv_porte_cidade (cod_porte, cod_situacao_cadastral);

COMMENT ON MATERIALIZED VIEW mv_porte_cidade IS 
'Contagem de empresas por porte (ME, EPP, Demais) por cidade.
Uso principal: Filtros por porte na busca avançada de empresas.
Códigos de porte: 00=Não informado, 01=ME, 03=EPP, 05=Demais.
Evita JOIN entre estabelecimento e empresa (~68M + 65M registros).';

