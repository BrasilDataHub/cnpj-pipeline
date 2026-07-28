-- =============================================================================
-- busca_estabelecimento_indexes.sql - Índices de prefixo e do hub CNAE×município
-- =============================================================================
-- Aplica na `busca_estabelecimento` JÁ CARREGADA os três índices acrescentados
-- em src/rfb_cnpj_etl/db/search_table.py (SEARCH_TABLE_INDEXES).
--
-- Quando usar ESTE arquivo:
--   quando a carga vigente é boa e você NÃO quer esperar a próxima carga
--   mensal. O `python etl.py db search` reconstrói a tabela inteira (CTAS +
--   swap) e já cria os três — mas leva horas.
--
-- Quando NÃO usar:
--   logo depois de um `db load`/`db complete`/`db search`. A tabela recém
--   construída já nasce com os índices; rodar aqui só devolve "já existe".
--
-- IMPORTANTE:
--   - CREATE INDEX CONCURRENTLY não bloqueia leitura nem escrita, mas
--     **não pode rodar dentro de transação**. Use `psql -f` (autocommit),
--     nunca `psql -1 -f` nem dentro de BEGIN/COMMIT.
--   - Se um CONCURRENTLY falhar no meio, ele deixa um índice INVÁLIDO para
--     trás. Confira ao final (a última query deste arquivo cobre isso) e
--     derrube com DROP INDEX CONCURRENTLY antes de tentar de novo.
--   - Idempotente (IF NOT EXISTS).
--   - Espaço adicional estimado: ~7 GB (detalhe em cada bloco). A tabela
--     ocupa hoje 12 GB de heap + 10 GB de índices.
--
-- Como aplicar:
--   psql -h <host> -U <user> -d dados_cnpj -f sql/busca_estabelecimento_indexes.sql
-- =============================================================================

\echo '=============================================================='
\echo 'Índices de busca_estabelecimento (prefixo + hub CNAE x municipio)'
\echo '=============================================================='
\echo ''

-- Acelera a construção. Vale por sessão e não afeta as demais conexões.
-- Não adianta mexer em max_parallel_maintenance_workers: build CONCURRENTLY
-- faz duas varreduras e espera as transações concorrentes, e é justamente o
-- caminho onde o paralelismo não entra.
SET maintenance_work_mem = '2GB';

-- -----------------------------------------------------------------------------
-- 1/3 - Razão social por PREFIXO (alta prioridade)
-- Usado em: website → EstablishmentRepository::applyCorporateNameFilter()
-- Query:    WHERE razao_social_norm LIKE 'TERMO%'
--
-- Medido na carga 2026-07, contra statement_timeout de 10 s:
--   count(*) WHERE razao_social_norm ILIKE 'MA%'  → 14.038 ms
--     Bitmap Index Scan em idx_busca_razao_social_trgm devolve 11.467.078
--     candidatos e o recheck descarta 9.915.193. O trigram casa em qualquer
--     posição, então um prefixo curto puxa toda linha que contenha "MA".
--   mesmo prefixo + situação + ORDER BY + LIMIT 20 → 14.005 ms
-- São as quatro issues de statement_timeout do Sentry em /api/v1/search.
--
-- text_pattern_ops é obrigatório: a collation do banco é en_US.utf8 e um
-- btree comum não serve LIKE fora da collation C.
--
-- ATENÇÃO — mudança pareada no website: este índice só é usado com `LIKE`.
-- Nenhuma classe de operador btree serve `ILIKE`. A coluna já é
-- unaccent(upper(...)) e o termo também, então a troca é semanticamente
-- neutra. Sem ela, o índice é criado e nunca escolhido.
--
-- Tamanho estimado: ~4,0 GB (72.318.968 linhas, comprimento médio 35 bytes).
-- -----------------------------------------------------------------------------
\echo '>>> [1/3] Criando idx_busca_razao_social_prefix ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_busca_razao_social_prefix
ON busca_estabelecimento (razao_social_norm text_pattern_ops);

\echo '    -> idx_busca_razao_social_prefix criado'

-- -----------------------------------------------------------------------------
-- 2/3 - Nome fantasia por PREFIXO (média prioridade)
-- Usado em: website → EstablishmentRepository::applyTradeNameFilter()
-- Query:    WHERE nome_fantasia_norm LIKE 'TERMO%'
--
-- Mesmo modo de falha, na borda do timeout:
--   count(*) WHERE nome_fantasia_norm ILIKE 'MA%' → 9.718 ms
--
-- Índice completo, e não parcial em `nome_fantasia_norm <> ''` (que seria
-- ~1,0 GB em vez de ~1,7 GB): o planner precisaria provar o predicado a
-- partir de um `LIKE`, e a prova acontece antes de o LIKE virar range —
-- o índice parcial correria o risco de nunca ser escolhido.
--
-- Tamanho estimado: ~1,7 GB (só 36,6% das linhas têm fantasia preenchida,
-- mas NULL e '' também ocupam entrada no btree).
-- -----------------------------------------------------------------------------
\echo '>>> [2/3] Criando idx_busca_nome_fantasia_prefix ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_busca_nome_fantasia_prefix
ON busca_estabelecimento (nome_fantasia_norm text_pattern_ops);

\echo '    -> idx_busca_nome_fantasia_prefix criado'

-- -----------------------------------------------------------------------------
-- 3/3 - Hub CNAE x municipio (alta prioridade)
-- Usado em: website → listagem de empresas em /municipio/{cidade}/cnae/{cnae}
-- Query:    WHERE cod_cidade_ibge = ? AND cod_cnae_principal = ?
--             AND cod_situacao_cadastral = '02'
--           ORDER BY cnpj_completo LIMIT 51 OFFSET ?
--
-- Nenhum índice cobre (cidade, CNAE): os existentes cobrem cidade OU CNAE.
-- O planner escolhe idx_busca_cidade_situacao_cnpj e joga o CNAE em Filter.
-- Medido em São Paulo x CNAE 8219999 (106.646 ativas, a maior combinação):
--   LIMIT 51 OFFSET 0     →    141 ms    7.313 buffers
--   LIMIT 51 OFFSET 4950  → 22.857 ms  308.325 buffers, 302.135 descartadas
-- Referência do que é bom — o mesmo query sem o predicado de CNAE, que usa o
-- índice por inteiro: 7,2 ms em OFFSET 24950, Index Only Scan, Heap Fetches 0.
--
-- Parcial porque a listagem só exibe ativos: 27.800.285 das 72.318.968 linhas.
-- O índice irmão completo (cidade, situação, cnpj) ocupa 2.802 MB.
--
-- Tamanho estimado: ~1,1 GB.
-- -----------------------------------------------------------------------------
\echo '>>> [3/3] Criando idx_busca_cidade_cnae_ativos ...'

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_busca_cidade_cnae_ativos
ON busca_estabelecimento (cod_cidade_ibge, cod_cnae_principal, cnpj_completo)
WHERE cod_situacao_cadastral = '02';

\echo '    -> idx_busca_cidade_cnae_ativos criado'

-- -----------------------------------------------------------------------------
-- Estatísticas
-- -----------------------------------------------------------------------------
\echo ''
\echo '>>> ANALYZE busca_estabelecimento ...'

ANALYZE busca_estabelecimento;

-- -----------------------------------------------------------------------------
-- Verificação
-- -----------------------------------------------------------------------------
\echo ''
\echo '=============================================================='
\echo 'Verificacao'
\echo '=============================================================='
\echo ''

SELECT
    indexname,
    pg_size_pretty(pg_relation_size(quote_ident(indexname)::regclass)) AS tamanho
FROM pg_indexes
WHERE tablename = 'busca_estabelecimento'
ORDER BY indexname;

\echo ''
\echo 'Indices INVALIDOS (esperado: nenhuma linha). Um CONCURRENTLY que falhou'
\echo 'no meio deixa o indice invalido, que ocupa espaco e nao e usado:'
\echo ''

SELECT
    c.relname AS indice,
    'DROP INDEX CONCURRENTLY ' || quote_ident(c.relname) || ';' AS como_remover
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
WHERE t.relname = 'busca_estabelecimento'
  AND NOT i.indisvalid;

\echo ''
\echo 'Proximo passo no website: trocar ILIKE por LIKE nos filtros de prefixo'
\echo 'de razao social e nome fantasia. Sem isso os indices 1 e 2 nao sao usados.'
\echo ''
