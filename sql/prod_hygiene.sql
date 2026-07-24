-- =============================================================================
-- HIGIENE DO BANCO DE PRODUÇÃO ATUAL (dados_cnpj) — AG10 / executado na F6
-- =============================================================================
--
-- Aplica ao banco EXISTENTE, sem recarga, as mudanças que o ETL já incorpora
-- nas próximas cargas (AG8/AG9): extensões, durabilidade (SET LOGGED),
-- remoção da leva 1 de índices redundantes e timeouts de servidor para o
-- usuário de leitura do website.
--
-- PRÉ-CONDIÇÕES (não executar sem todas):
--   1. Backup existente e RESTAURÁVEL (tarefa F1: snapshot + dump testado).
--   2. Janela de baixo tráfego (madrugada BRT). O SET LOGGED reescreve cada
--      tabela com WAL e segura lock exclusivo durante a reescrita.
--   3. Espaço em disco: a reescrita precisa de espaço livre ≈ tamanho da
--      maior tabela (estabelecimento: ~15 GB de dados) + WAL (max_wal_size).
--   4. Desejável: imagem custom do infra/ já implantada (F4) com
--      shared_preload_libraries=pg_stat_statements — sem isso o CREATE
--      EXTENSION funciona, mas a coleta de estatísticas fica inativa.
--
-- COMO EXECUTAR:
--   psql "host=... dbname=dados_cnpj user=postgres" \
--        -v ON_ERROR_STOP=1 -f prod_hygiene.sql
--
--   * NÃO envolver em BEGIN/COMMIT: DROP INDEX CONCURRENTLY não roda dentro
--     de transação (o psql em autocommit está correto).
--   * O script é idempotente: pode ser reexecutado do zero se interrompido.
--
-- DURAÇÃO ESTIMADA POR PASSO (hardware atual — CCX13, volume de rede):
--   Passo 1 (extensões):        segundos.
--   Passo 2 (SET LOGGED):       1–3 h no total, tabela a tabela, na ordem
--                               da menor para a maior (simples → socio →
--                               cnae_sec → empresa → estabelecimento).
--   Passo 3 (DROP INDEX):       minutos (CONCURRENTLY, sem lock de escrita).
--   Passo 4 (timeouts do role): segundos.
--
-- ORDEM SEGURA: os passos são independentes; a ordem abaixo prioriza o que
-- elimina risco de perda de dados (LOGGED) antes da limpeza de índices.
-- Se a janela fechar, é seguro parar entre passos e retomar depois.
-- =============================================================================

\timing on

-- =============================================================================
-- PASSO 1 — Extensões (segundos, sem lock relevante)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- =============================================================================
-- PASSO 2 — Durabilidade: UNLOGGED → LOGGED (1–3 h, lock exclusivo por tabela)
--
-- Tabela UNLOGGED é TRUNCADA pelo PostgreSQL no recovery após crash — hoje
-- um crash perde a base inteira. LOGGED também é pré-condição para backup
-- físico/PITR e réplicas. O comando é no-op em tabela já LOGGED (idempotente).
-- Ordem: da menor para a maior, para liberar risco cedo e falhar cedo se
-- faltar espaço/WAL.
-- =============================================================================

ALTER TABLE public.simples SET LOGGED;
ALTER TABLE public.socio SET LOGGED;
ALTER TABLE public.estabelecimento_cnae_sec SET LOGGED;
ALTER TABLE public.empresa SET LOGGED;
ALTER TABLE public.estabelecimento SET LOGGED;

-- Tabelas pequenas (catálogos) — segundos no total, mesmo risco de truncamento.
ALTER TABLE public.cnae SET LOGGED;
ALTER TABLE public.motivo SET LOGGED;
ALTER TABLE public.municipio_rfb SET LOGGED;
ALTER TABLE public.ibge_regiao SET LOGGED;
ALTER TABLE public.ibge_estado SET LOGGED;
ALTER TABLE public.ibge_cidade SET LOGGED;
ALTER TABLE public.natureza_juridica SET LOGGED;
ALTER TABLE public.pais SET LOGGED;
ALTER TABLE public.qualificacao_socio SET LOGGED;

-- Verificação: deve retornar 0 linhas.
SELECT c.relname AS ainda_unlogged
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relpersistence = 'u';

-- =============================================================================
-- PASSO 3 — Remoção da LEVA 1 de índices redundantes (~7,5 GB liberados)
--
-- Apenas duplicatas exatas: cada índice removido tem um "gêmeo" que dá a
-- mesma capacidade (collation C torna varchar_pattern_ops redundante; a PK
-- btree cobre a igualdade do hash). A LEVA 2 (prefixos de compostos) NÃO
-- está aqui: exige pg_stat_user_indexes.idx_scan ≈ 0 por 30 dias com
-- pg_stat_statements ativo — protocolo em docs/index_cleanup.md.
--
-- CONCURRENTLY: sem lock de escrita; não pode rodar dentro de transação.
-- =============================================================================

DROP INDEX CONCURRENTLY IF EXISTS idx_empresa_razao_social_prefix;   -- 3,7 GB, duplica idx_empresa_razao_social
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_nome_fantasia_prefix;    -- 1,0 GB, duplica idx_estab_nome_fantasia
DROP INDEX CONCURRENTLY IF EXISTS idx_socio_nome_prefix;             -- 0,8 GB, duplica idx_socio_nome
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_cnpj_completo_hash;      -- 2,0 GB, duplica a PK para igualdade

-- ROLLBACK (recriação, se alguma consulta regredir — rodar fora de transação):
-- CREATE INDEX CONCURRENTLY idx_empresa_razao_social_prefix ON public.empresa (razao_social varchar_pattern_ops);
-- CREATE INDEX CONCURRENTLY idx_estab_nome_fantasia_prefix ON public.estabelecimento (nome_fantasia varchar_pattern_ops);
-- CREATE INDEX CONCURRENTLY idx_socio_nome_prefix ON public.socio (nome_socio varchar_pattern_ops);
-- CREATE INDEX CONCURRENTLY idx_estab_cnpj_completo_hash ON public.estabelecimento USING HASH (cnpj_completo);

-- =============================================================================
-- PASSO 4 — Timeouts de servidor para o usuário de leitura do website
--
-- Hoje só a aplicação impõe timeout (SET LOCAL statement_timeout=10s). Este
-- guarda-chuva do lado do servidor cobre qualquer caminho que escape da
-- aplicação (sessões manuais, bugs, ferramentas). 15 s > 10 s da aplicação:
-- o limite do servidor é a rede de segurança, não o primário.
-- Guardado por existência do role (ambiente local pode não tê-lo).
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dados_read') THEN
        ALTER ROLE dados_read SET statement_timeout = '15s';
        ALTER ROLE dados_read SET idle_in_transaction_session_timeout = '60s';
        RAISE NOTICE 'Timeouts aplicados ao role dados_read.';
    ELSE
        RAISE NOTICE 'Role dados_read não existe neste banco — passo 4 pulado.';
    END IF;
END
$$;

-- ROLLBACK do passo 4:
-- ALTER ROLE dados_read RESET statement_timeout;
-- ALTER ROLE dados_read RESET idle_in_transaction_session_timeout;

-- =============================================================================
-- VERIFICAÇÃO FINAL
-- =============================================================================

SELECT extname FROM pg_extension
WHERE extname IN ('pg_trgm', 'unaccent', 'pg_stat_statements')
ORDER BY extname;                                        -- esperado: 3 linhas

SELECT count(*) AS tabelas_unlogged
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relpersistence = 'u';
                                                         -- esperado: 0

SELECT indexname FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN ('idx_empresa_razao_social_prefix', 'idx_estab_nome_fantasia_prefix',
                    'idx_socio_nome_prefix', 'idx_estab_cnpj_completo_hash');
                                                         -- esperado: 0 linhas
