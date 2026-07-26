-- =============================================================================
-- mv_stats_cnae - Estatísticas agregadas por CNAE
-- =============================================================================
-- Tempo estimado de criação: ~3 min
-- Periodicidade de refresh recomendada: Diário
-- Depende de: 00_helpers.sql (fn_mes_ancora)
-- =============================================================================
-- MUDANÇA DE SEMÂNTICA — as janelas `novos_*` deixaram de usar CURRENT_DATE e
-- passaram a ser ancoradas no último mês completo da carga (fn_mes_ancora), o
-- que torna o build reprodutível e exclui o mês parcial. Esta MV já contava
-- todas as aberturas (sem filtro de situação), então só o recorte mudou.
-- Ver o cabeçalho de 01_mv_stats_estado.sql.
-- =============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_stats_cnae CASCADE;

CREATE MATERIALIZED VIEW mv_stats_cnae AS
SELECT
    e.cod_cnae_principal,
    c.nome_cnae,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(DISTINCT e.cod_estado_ibge) AS estados_presentes,
    COUNT(DISTINCT e.cod_cidade_ibge) AS cidades_presentes,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_1mes
        AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_1mes,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_6meses
        AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_1ano
        AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_2anos
        AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= j.ini_4anos
        AND e.data_inicio_atividade < j.fim_exclusivo) AS novos_4anos,
    j.mes_ancora
FROM estabelecimento e
LEFT JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
CROSS JOIN (
    SELECT a.mes_ancora,
           a.mes_ancora                                  AS ini_1mes,
           (a.mes_ancora - INTERVAL '5 months')::DATE    AS ini_6meses,
           (a.mes_ancora - INTERVAL '11 months')::DATE   AS ini_1ano,
           (a.mes_ancora - INTERVAL '23 months')::DATE   AS ini_2anos,
           (a.mes_ancora - INTERVAL '47 months')::DATE   AS ini_4anos,
           (a.mes_ancora + INTERVAL '1 month')::DATE     AS fim_exclusivo
      FROM (SELECT fn_mes_ancora() AS mes_ancora) a
) j
GROUP BY e.cod_cnae_principal, c.nome_cnae, j.mes_ancora;

-- Índices
CREATE UNIQUE INDEX idx_mv_stats_cnae_pk
    ON mv_stats_cnae (cod_cnae_principal);
CREATE INDEX idx_mv_stats_cnae_total
    ON mv_stats_cnae (total_estabelecimentos DESC);

COMMENT ON COLUMN mv_stats_cnae.novos_1mes IS 'Aberturas no mês da âncora (último mês completo da carga)';
COMMENT ON COLUMN mv_stats_cnae.mes_ancora IS 'Último mês completo da carga usado nas janelas novos_* (fn_mes_ancora)';
