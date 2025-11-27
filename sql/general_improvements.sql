-- =============================================================================
-- general_improvements.sql - Configurações e Otimizações Gerais
-- =============================================================================
-- Este script contém:
-- 1. Extensões necessárias para funcionalidades avançadas
-- 2. Configurações de sessão para performance
-- 3. Sequência de comandos pós-carga (VACUUM, ANALYZE, etc.)
-- 4. Funções utilitárias de manutenção
-- 5. Validações de integridade
--
-- IMPORTANTE: 
--   - Executar APÓS a carga completa dos dados (após `db fk` ou `complete`)
--   - Este script deve ser executado em etapas conforme indicado
--   - Algumas operações requerem permissões de superusuário
-- =============================================================================

\echo '=============================================================='
\echo 'Configurações e Otimizações Gerais para Base CNPJ'
\echo '=============================================================='

-- =============================================================================
-- SEÇÃO 1: EXTENSÕES NECESSÁRIAS
-- =============================================================================
\echo ''
\echo '>>> [1/5] Instalando extensões necessárias...'

-- Extensão para busca textual com trigramas (ILIKE performático)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Extensão para pré-aquecimento de índices em cache
CREATE EXTENSION IF NOT EXISTS pg_prewarm;

-- Extensão para estatísticas detalhadas de tabelas
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

\echo '    -> Extensões instaladas: pg_trgm, pg_prewarm, pg_stat_statements'

-- =============================================================================
-- SEÇÃO 2: CONFIGURAÇÕES DE SESSÃO PARA CARGA MASSIVA
-- =============================================================================
\echo ''
\echo '>>> [2/5] Configurações recomendadas para carga massiva...'
\echo ''
\echo 'Para carga inicial, execute estes comandos no servidor PostgreSQL:'
\echo ''
\echo '  -- postgresql.conf (durante carga)'
\echo '  shared_buffers = 4GB              -- 25% da RAM'
\echo '  work_mem = 256MB                  -- Para sorts e hashes'
\echo '  maintenance_work_mem = 2GB        -- Para VACUUM e CREATE INDEX'
\echo '  effective_cache_size = 12GB       -- 75% da RAM'
\echo '  max_parallel_maintenance_workers = 4'
\echo '  max_parallel_workers_per_gather = 4'
\echo '  checkpoint_completion_target = 0.9'
\echo '  wal_buffers = 64MB'
\echo '  synchronous_commit = off          -- APENAS durante carga!'
\echo '  autovacuum = off                  -- Reativar após carga'
\echo ''

-- Configurações de sessão (aplicáveis agora)
SET max_parallel_maintenance_workers = 4;
SET maintenance_work_mem = '2GB';

-- =============================================================================
-- SEÇÃO 3: SEQUÊNCIA PÓS-CARGA
-- =============================================================================
\echo ''
\echo '>>> [3/5] Sequência de comandos pós-carga...'
\echo ''
\echo 'Execute os comandos abaixo APÓS a carga completa dos dados:'
\echo ''

-- Bloco de comandos pós-carga (comentado para execução manual)
\echo '-- 3.1 VACUUM FULL para recuperar espaço (tabelas grandes)'
\echo 'VACUUM FULL estabelecimento;'
\echo 'VACUUM FULL empresa;'
\echo 'VACUUM FULL estabelecimento_cnae_sec;'
\echo 'VACUUM FULL socio;'
\echo 'VACUUM FULL simples;'
\echo ''
\echo '-- 3.2 ANALYZE para estatísticas precisas'
\echo 'ANALYZE estabelecimento;'
\echo 'ANALYZE empresa;'
\echo 'ANALYZE estabelecimento_cnae_sec;'
\echo 'ANALYZE socio;'
\echo 'ANALYZE simples;'
\echo ''
\echo '-- 3.3 Reativar autovacuum (IMPORTANTE!)'
\echo 'ALTER TABLE estabelecimento SET (autovacuum_enabled = true);'
\echo 'ALTER TABLE empresa SET (autovacuum_enabled = true);'
\echo 'ALTER TABLE estabelecimento_cnae_sec SET (autovacuum_enabled = true);'
\echo 'ALTER TABLE socio SET (autovacuum_enabled = true);'
\echo 'ALTER TABLE simples SET (autovacuum_enabled = true);'
\echo ''

-- =============================================================================
-- SEÇÃO 4: FUNÇÕES DE MANUTENÇÃO
-- =============================================================================
\echo ''
\echo '>>> [4/5] Criando funções utilitárias de manutenção...'

-- Função para pré-aquecer índices críticos no cache
CREATE OR REPLACE FUNCTION prewarm_critical_indexes()
RETURNS TABLE (
    index_name TEXT,
    blocks_loaded BIGINT
) AS $$
DECLARE
    idx RECORD;
    loaded BIGINT;
BEGIN
    -- Lista de índices críticos para pré-aquecimento
    FOR idx IN 
        SELECT indexrelid::regclass::text as idx_name
        FROM pg_index
        WHERE indrelid IN (
            'estabelecimento'::regclass,
            'empresa'::regclass,
            'estabelecimento_cnae_sec'::regclass
        )
        AND indisvalid = true
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 20  -- Top 20 maiores índices
    LOOP
        BEGIN
            SELECT pg_prewarm(idx.idx_name::regclass) INTO loaded;
            index_name := idx.idx_name;
            blocks_loaded := loaded;
            RETURN NEXT;
        EXCEPTION WHEN OTHERS THEN
            index_name := idx.idx_name;
            blocks_loaded := 0;
            RETURN NEXT;
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION prewarm_critical_indexes() IS 
'Pré-aquece os índices mais importantes no buffer cache.
Executar após restart do servidor ou quando performance degradar.
Uso: SELECT * FROM prewarm_critical_indexes();';

-- Função para executar VACUUM ANALYZE em todas as tabelas principais
CREATE OR REPLACE FUNCTION vacuum_analyze_all()
RETURNS TABLE (
    table_name TEXT,
    status TEXT,
    duration INTERVAL
) AS $$
DECLARE
    tbl TEXT;
    start_time TIMESTAMP;
    end_time TIMESTAMP;
    tables TEXT[] := ARRAY[
        'estabelecimento', 'empresa', 'estabelecimento_cnae_sec',
        'socio', 'simples', 'cnae', 'pais', 'municipio_rfb',
        'natureza_juridica', 'qualificacao_socio', 'motivo',
        'ibge_regiao', 'ibge_estado', 'ibge_cidade'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables
    LOOP
        BEGIN
            start_time := clock_timestamp();
            EXECUTE format('VACUUM (ANALYZE, VERBOSE) %I', tbl);
            end_time := clock_timestamp();
            table_name := tbl;
            status := 'OK';
            duration := end_time - start_time;
            RETURN NEXT;
        EXCEPTION WHEN OTHERS THEN
            table_name := tbl;
            status := SQLERRM;
            duration := NULL;
            RETURN NEXT;
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION vacuum_analyze_all() IS 
'Executa VACUUM ANALYZE em todas as tabelas principais.
Uso: SELECT * FROM vacuum_analyze_all();';

-- Função para verificar estatísticas das tabelas principais
CREATE OR REPLACE FUNCTION table_statistics()
RETURNS TABLE (
    table_name TEXT,
    row_count BIGINT,
    total_size TEXT,
    index_size TEXT,
    last_vacuum TIMESTAMP,
    last_analyze TIMESTAMP
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        t.relname::TEXT,
        t.n_live_tup,
        pg_size_pretty(pg_total_relation_size(t.relid)),
        pg_size_pretty(pg_indexes_size(t.relid)),
        t.last_vacuum,
        t.last_analyze
    FROM pg_stat_user_tables t
    WHERE t.schemaname = 'public'
    ORDER BY pg_total_relation_size(t.relid) DESC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION table_statistics() IS 
'Retorna estatísticas de todas as tabelas do schema public.
Uso: SELECT * FROM table_statistics();';

\echo '    -> Funções criadas: prewarm_critical_indexes, vacuum_analyze_all, table_statistics'

-- =============================================================================
-- SEÇÃO 5: VALIDAÇÕES DE INTEGRIDADE
-- =============================================================================
\echo ''
\echo '>>> [5/5] Criando funções de validação...'

-- Função para validar CNPJ completo
CREATE OR REPLACE FUNCTION validate_cnpj_completo()
RETURNS TABLE (
    check_name TEXT,
    table_name TEXT,
    count BIGINT,
    status TEXT
) AS $$
BEGIN
    -- Verificar NULLs em estabelecimento
    RETURN QUERY
    SELECT 
        'cnpj_completo_nulls'::TEXT,
        'estabelecimento'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FALHA: NULLs encontrados' END
    FROM estabelecimento 
    WHERE cnpj_completo IS NULL OR cnpj_completo = '';

    -- Verificar formato (14 caracteres)
    RETURN QUERY
    SELECT 
        'cnpj_completo_format'::TEXT,
        'estabelecimento'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FALHA: Formato incorreto' END
    FROM estabelecimento 
    WHERE LENGTH(cnpj_completo) != 14;

    -- Verificar consistência com componentes
    RETURN QUERY
    SELECT 
        'cnpj_completo_consistency'::TEXT,
        'estabelecimento'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'AVISO: Inconsistências encontradas' END
    FROM estabelecimento 
    WHERE cnpj_completo != (cnpj_basico || cnpj_ordem || cnpj_dv);

    -- Verificar NULLs em cnae_sec
    RETURN QUERY
    SELECT 
        'cnpj_completo_nulls'::TEXT,
        'estabelecimento_cnae_sec'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FALHA: NULLs encontrados' END
    FROM estabelecimento_cnae_sec 
    WHERE cnpj_completo IS NULL OR cnpj_completo = '';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION validate_cnpj_completo() IS 
'Valida integridade da coluna cnpj_completo em todas as tabelas.
Uso: SELECT * FROM validate_cnpj_completo();';

-- Função para verificar integridade referencial
CREATE OR REPLACE FUNCTION check_referential_integrity()
RETURNS TABLE (
    fk_name TEXT,
    source_table TEXT,
    orphan_count BIGINT,
    status TEXT
) AS $$
BEGIN
    -- Estabelecimento -> Empresa
    RETURN QUERY
    SELECT 
        'fk_estab_empresa'::TEXT,
        'estabelecimento'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'AVISO: Registros órfãos' END
    FROM estabelecimento e
    WHERE NOT EXISTS (SELECT 1 FROM empresa emp WHERE emp.cnpj_basico = e.cnpj_basico);

    -- Estabelecimento -> CNAE
    RETURN QUERY
    SELECT 
        'fk_estab_cnae'::TEXT,
        'estabelecimento'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'AVISO: CNAEs não encontrados' END
    FROM estabelecimento e
    WHERE NOT EXISTS (SELECT 1 FROM cnae c WHERE c.cod_cnae = e.cod_cnae_principal);

    -- Sócio -> Empresa
    RETURN QUERY
    SELECT 
        'fk_socio_empresa'::TEXT,
        'socio'::TEXT,
        COUNT(*)::BIGINT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'AVISO: Sócios órfãos' END
    FROM socio s
    WHERE NOT EXISTS (SELECT 1 FROM empresa emp WHERE emp.cnpj_basico = s.cnpj_basico);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_referential_integrity() IS 
'Verifica integridade referencial entre as tabelas principais.
Uso: SELECT * FROM check_referential_integrity();';

\echo '    -> Funções de validação criadas: validate_cnpj_completo, check_referential_integrity'

-- =============================================================================
-- FINALIZAÇÃO
-- =============================================================================
\echo ''
\echo '=============================================================='
\echo 'Configurações gerais aplicadas com sucesso!'
\echo '=============================================================='
\echo ''
\echo 'Extensões instaladas:'
\echo '  - pg_trgm: Busca textual com trigramas'
\echo '  - pg_prewarm: Pré-aquecimento de índices'
\echo '  - pg_stat_statements: Estatísticas de queries'
\echo ''
\echo 'Funções utilitárias disponíveis:'
\echo '  - prewarm_critical_indexes(): Pré-aquece índices no cache'
\echo '  - vacuum_analyze_all(): VACUUM ANALYZE em todas as tabelas'
\echo '  - table_statistics(): Estatísticas das tabelas'
\echo '  - validate_cnpj_completo(): Valida integridade do CNPJ completo'
\echo '  - check_referential_integrity(): Verifica FKs'
\echo ''
\echo 'Próximos passos:'
\echo '  1. Execute sql/indexes.sql para criar índices otimizados'
\echo '  2. Execute: python etl.py db views create (para criar MVs)'
\echo '  3. Execute SELECT * FROM vacuum_analyze_all();'
\echo '  4. Execute SELECT * FROM prewarm_critical_indexes();'
\echo '  5. Execute SELECT * FROM validate_cnpj_completo();'
\echo ''

