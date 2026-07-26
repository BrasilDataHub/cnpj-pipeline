-- =============================================================================
-- refresh_all_mvs - Funcao para atualizar todas as Materialized Views
-- =============================================================================
-- Esta funcao pode ser chamada via: SELECT * FROM refresh_all_mvs();
-- Recomendado executar quinzenalmente (dias 1 e 15) as 03:00
-- =============================================================================
-- A versao anterior tinha um unico bloco EXCEPTION no fim: a primeira MV que
-- falhasse abortava TODAS as seguintes e a funcao devolvia uma linha 'ERROR'
-- sem dizer quais tinham dado certo. Agora cada MV tem seu proprio
-- BEGIN...EXCEPTION e o resultado sempre traz uma linha por MV.
--
-- CONCURRENTLY e decidido em tempo de execucao consultando pg_indexes (a MV
-- precisa de indice unico). Antes a escolha era escrita a mao no SQL e ficava
-- desatualizada a cada indice novo — foi o que aconteceu com
-- mv_stats_natureza_juridica_cnae, que ganhou indice unico e continuava
-- bloqueando leituras no refresh.
--
-- ORDEM: estoques -> series mensais -> comparativos. Os comparativos LEEM as
-- series (17 le 05; 18 le 14; 19 le 15); refrescar na ordem inversa
-- publicaria comparativo velho sobre serie nova.
-- =============================================================================

CREATE OR REPLACE FUNCTION refresh_all_mvs()
RETURNS TABLE (
    mv_name TEXT,
    status TEXT,
    duration INTERVAL
) AS $$
DECLARE
    -- Ordem de refresh (dependencias primeiro). MVs ausentes viram linha
    -- 'AUSENTE' em vez de erro: bases antigas podem nao ter todas.
    mvs_ordenadas CONSTANT TEXT[] := ARRAY[
        -- Estoques: nao dependem de nenhuma outra MV
        'mv_stats_estado',
        'mv_stats_cnae',
        'mv_stats_natureza_juridica',
        'mv_stats_cidade_situacao',
        'mv_stats_natureza_juridica_cnae',
        'mv_regime_tributario_cidade',
        'mv_porte_cidade',
        'mv_stats_municipio',
        'mv_stats_cnae_estado',
        'mv_stats_natureza_juridica_estado',
        'mv_stats_natureza_juridica_municipio',
        'mv_top_cnaes_cidade',
        -- Series mensais
        'mv_abertura_periodo',
        'mv_movimentacao_mensal_cnae',
        'mv_movimentacao_mensal_natureza',
        'mv_movimentacao_mensal_porte',
        -- Comparativos: leem as series acima
        'mv_comparativo_territorio',
        'mv_comparativo_cnae',
        'mv_comparativo_natureza'
    ];
    alvo TEXT;
    tem_indice_unico BOOLEAN;
    inicio TIMESTAMP;
BEGIN
    FOREACH alvo IN ARRAY mvs_ordenadas LOOP
        IF to_regclass('public.' || alvo) IS NULL THEN
            mv_name := alvo;
            status := 'AUSENTE';
            duration := NULL;
            RETURN NEXT;
            CONTINUE;
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
             WHERE schemaname = 'public'
               AND tablename = alvo
               AND indexdef LIKE 'CREATE UNIQUE INDEX%'
        ) INTO tem_indice_unico;

        inicio := clock_timestamp();
        BEGIN
            IF tem_indice_unico THEN
                EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %I', alvo);
            ELSE
                EXECUTE format('REFRESH MATERIALIZED VIEW %I', alvo);
            END IF;
            mv_name := alvo;
            status := 'OK';
        EXCEPTION WHEN OTHERS THEN
            -- Falha isolada: registra e segue para a proxima MV.
            mv_name := alvo;
            status := 'ERRO: ' || SQLERRM;
        END;
        duration := clock_timestamp() - inicio;
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_all_mvs() IS
'Atualiza todas as Materialized Views de estatisticas, uma linha de resultado
por MV (OK / ERRO: <mensagem> / AUSENTE). Uma falha nao interrompe as demais.
CONCURRENTLY e escolhido automaticamente quando a MV tem indice unico.
Recomendado executar quinzenalmente (dias 1 e 15) as 03:00.
Uso: SELECT * FROM refresh_all_mvs();';
