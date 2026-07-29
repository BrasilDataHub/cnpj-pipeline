-- =============================================================================
-- work_mem por role e o teto de tempo por superfície — item 32 do roadmap 20
-- =============================================================================
-- O PORQUÊ, em uma frase: `work_mem` é POR OPERAÇÃO de sort/hash, e um valor
-- que é certo para o ETL é catastrófico para o site.
--
-- O perfil `compartilhada-14gb` sobe `work_mem` de 32 para 96 MB, porque o ETL
-- e o build das 19 MVs derramam em disco — 9.545 derrames de ~37 MB medidos,
-- ~706 GB de IO evitável. Mas o MESMO 96 MB vale para uma consulta do site: um
-- `GROUP BY` malfeito numa página pública passa a poder alocar 96 MB × o número
-- de nós de hash do plano, e com paralelismo isso vira quase 1 GB numa
-- requisição anônima.
--
-- A saída não é escolher um número: é ter dois. `ALTER ROLE ... SET` aplica no
-- login, sem tocar no default do cluster, e cada superfície fica com o valor que
-- faz sentido para ela.
--
-- Aplicar no banco de dados, uma vez. Idempotente.
-- =============================================================================

\set ON_ERROR_STOP on

-- -----------------------------------------------------------------------------
-- dados_read — a role que o SITE usa
-- -----------------------------------------------------------------------------
-- Ela já existe (initdb/03-role-metrics.sh e o script de higiene). O que muda é
-- o par de limites.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dados_read') THEN
        RAISE NOTICE 'role dados_read não existe neste banco; pulando';
        RETURN;
    END IF;

    -- 32 MB: o suficiente para as consultas que a aplicação realmente faz
    -- (hub cidade+CNAE em 0,677 ms, CNPJ em 0,122 ms), e pouco o bastante para
    -- que uma consulta ruim NÃO consiga alocar 1 GB.
    EXECUTE 'ALTER ROLE dados_read SET work_mem = ''32MB''';

    -- 5 segundos. Uma consulta de origem pública que passa disso não vai
    -- terminar de forma útil: o SLO da busca avançada é 200 ms no p95, e o
    -- visitante já foi embora. O teto transforma "o site travou" em "uma
    -- consulta falhou", que é infinitamente mais fácil de diagnosticar.
    --
    -- Isto é redundante com o `SET LOCAL` de App\Support\StatementTimeout, e a
    -- redundância é deliberada: aquele protege o caminho que passa pelo
    -- repositório, este protege QUALQUER caminho, inclusive um `DB::select`
    -- solto que alguém escreva amanhã.
    EXECUTE 'ALTER ROLE dados_read SET statement_timeout = ''5s''';

    -- Uma transação de leitura aberta por horas segura o xmin e impede o
    -- autovacuum de limpar tupla morta de NENHUMA tabela. O sintoma aparece
    -- dias depois, como disco cheio.
    EXECUTE 'ALTER ROLE dados_read SET idle_in_transaction_session_timeout = ''60s''';

    RAISE NOTICE 'dados_read: work_mem=32MB statement_timeout=5s';
END $$;

-- -----------------------------------------------------------------------------
-- dados_export — a role das exportações e do sitemap
-- -----------------------------------------------------------------------------
-- Consultas legitimamente longas e legitimamente pesadas, e que NÃO servem
-- ninguém esperando na frente de uma tela. Separá-las de `dados_read` é o que
-- permite dar a cada uma o limite certo em vez de um meio-termo que não serve
-- para nenhuma.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dados_export') THEN
        EXECUTE 'CREATE ROLE dados_export LOGIN';
        RAISE NOTICE 'role dados_export criada (defina a senha no deploy)';
    END IF;

    EXECUTE 'GRANT USAGE ON SCHEMA public TO dados_export';
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO dados_export';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dados_export';

    -- 64 MB: mais que o site, menos que o ETL. Uma exportação de 200 mil linhas
    -- ordena de verdade.
    EXECUTE 'ALTER ROLE dados_export SET work_mem = ''64MB''';
    -- 30 minutos. É o tempo de uma exportação grande; abaixo disso o job
    -- morreria no meio e reprocessaria do zero.
    EXECUTE 'ALTER ROLE dados_export SET statement_timeout = ''30min''';
    -- Paralelismo reduzido: a exportação não pode tomar os workers que o site
    -- usa. Ela pode esperar; o visitante, não.
    EXECUTE 'ALTER ROLE dados_export SET max_parallel_workers_per_gather = 1';

    RAISE NOTICE 'dados_export: work_mem=64MB statement_timeout=30min';
END $$;

-- -----------------------------------------------------------------------------
-- Conferência
-- -----------------------------------------------------------------------------
SELECT r.rolname, unnest(r.rolconfig) AS configuracao
FROM pg_roles r
WHERE r.rolname IN ('dados_read', 'dados_export')
ORDER BY r.rolname, configuracao;
