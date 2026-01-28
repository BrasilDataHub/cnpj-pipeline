-- =============================================================================
-- refresh_all_mvs - Funcao para atualizar todas as Materialized Views
-- =============================================================================
-- Esta funcao pode ser chamada via: SELECT * FROM refresh_all_mvs();
-- Recomendado executar quinzenalmente (dias 1 e 15) as 03:00
-- =============================================================================

CREATE OR REPLACE FUNCTION refresh_all_mvs()
RETURNS TABLE (
    mv_name TEXT,
    status TEXT,
    duration INTERVAL
) AS $$
DECLARE
    start_time TIMESTAMP;
    end_time TIMESTAMP;
BEGIN
    -- MVs menores primeiro (mais rapido)
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_estado;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_estado';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_cnae';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_natureza_juridica';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cidade_situacao;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_cidade_situacao';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW mv_stats_natureza_juridica_cnae;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_natureza_juridica_cnae';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_regime_tributario_cidade;
    end_time := clock_timestamp();
    mv_name := 'mv_regime_tributario_cidade';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_porte_cidade;
    end_time := clock_timestamp();
    mv_name := 'mv_porte_cidade';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    -- MVs maiores
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_municipio;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_municipio';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae_estado;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_cnae_estado';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica_estado;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_natureza_juridica_estado';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_natureza_juridica_municipio;
    end_time := clock_timestamp();
    mv_name := 'mv_stats_natureza_juridica_municipio';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_abertura_periodo;
    end_time := clock_timestamp();
    mv_name := 'mv_abertura_periodo';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    -- mv_top_cnaes_cidade nao possui indice unico, refresh sem CONCURRENTLY
    start_time := clock_timestamp();
    REFRESH MATERIALIZED VIEW mv_top_cnaes_cidade;
    end_time := clock_timestamp();
    mv_name := 'mv_top_cnaes_cidade';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

EXCEPTION WHEN OTHERS THEN
    mv_name := 'ERROR';
    status := SQLERRM;
    duration := NULL;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_all_mvs() IS
'Atualiza todas as Materialized Views de estatisticas.
Recomendado executar quinzenalmente (dias 1 e 15) as 03:00.
Uso: SELECT * FROM refresh_all_mvs();';
