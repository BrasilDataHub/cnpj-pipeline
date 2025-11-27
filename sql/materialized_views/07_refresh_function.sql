-- =============================================================================
-- refresh_all_mvs - Função para atualizar todas as Materialized Views
-- =============================================================================
-- Esta função pode ser chamada via: SELECT * FROM refresh_all_mvs();
-- Recomendado executar quinzenalmente (dias 1 e 15) às 03:00
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
    -- MVs menores primeiro (mais rápido)
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
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_abertura_periodo;
    end_time := clock_timestamp();
    mv_name := 'mv_abertura_periodo';
    status := 'OK';
    duration := end_time - start_time;
    RETURN NEXT;

    -- mv_top_cnaes_cidade não possui índice único, refresh sem CONCURRENTLY
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
'Atualiza todas as Materialized Views de estatísticas.
Recomendado executar quinzenalmente (dias 1 e 15) às 03:00.
Uso: SELECT * FROM refresh_all_mvs();';

