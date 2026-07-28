-- =============================================================================
-- Drop dos índices sem uso — item 13 do roadmap 20
-- =============================================================================
-- Libera 34.8 GB de 134 GB. Menos disputa de page cache — que é o gargalo
-- medido do host — e um ETL mais leve, porque cada índice é mantido a cada
-- carga.
--
-- PRÉ-CONDIÇÕES, nesta ordem:
--   1. `sql/indices_sem_uso_2026-07-28.sql` versionado (já está, no mesmo
--      diretório): sem ele, um índice dropado por engano não tem como voltar.
--   2. Backup do item 0 funcionando. Isto é DDL sobre um banco de 134 GB.
--   3. Ler a ressalva metodológica de 01 §6.6 — reproduzida no arquivo de DDL.
--
-- `CONCURRENTLY` em todos: um `DROP INDEX` comum pega ACCESS EXCLUSIVE na
-- TABELA, não só no índice, e bloquearia toda leitura de `estabelecimento`
-- pelo tempo do drop. Com CONCURRENTLY não há bloqueio — em troca, o comando
-- NÃO pode rodar dentro de transação. Por isso este arquivo não tem BEGIN/COMMIT
-- e deve ser executado com `psql -f`, nunca colado num bloco transacional.
--
-- Se um DROP CONCURRENTLY for interrompido, o índice fica INVÁLIDO e continua
-- ocupando disco. Conferir depois:
--
--   SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
--
-- =============================================================================
-- O QUE NÃO ESTÁ AQUI, E POR QUÊ
--
-- 6 dos 67 índices com idx_scan = 0 foram EXCLUÍDOS da lista:
--
--   empresa_pkey                         2077.5 MB  chave primária
--   idx_mv_mov_porte_pk                     0.8 MB  índice único de MV — REFRESH CONCURRENTLY depende dele
--   idx_mv_nj_cnae_pk                       0.4 MB  índice único de MV — REFRESH CONCURRENTLY depende dele
--   idx_mv_stats_estado_pk                  0.0 MB  índice único de MV — REFRESH CONCURRENTLY depende dele
--   ibge_regiao_sigla_regiao_key            0.0 MB  sustenta constraint UNIQUE
--   ibge_estado_sigla_uf_key                0.0 MB  sustenta constraint UNIQUE
--
-- Os três `*_pk` de MV merecem destaque: são índices ÚNICOS sobre materialized
-- views, e `sql/materialized_views/99_refresh_function.sql` escolhe
-- `REFRESH MATERIALIZED VIEW CONCURRENTLY` **consultando pg_indexes**. Dropá-los
-- não quebra nada de imediato — degrada o refresh mensal para a forma que
-- BLOQUEIA leitura da MV inteira enquanto roda. Um efeito colateral que só
-- apareceria na próxima carga, como indisponibilidade.
--
-- `empresa_pkey` aparece com idx_scan = 0 e tem 2 GB. É a chave primária de
-- `empresa`: o contador está zerado porque as consultas de empresa entram por
-- `estabelecimento` e por `busca_estabelecimento`, não porque a PK seja
-- dispensável.
-- =============================================================================

\set ON_ERROR_STOP on
\timing on

-- 4750.1 MB · estabelecimento_cnae_sec
DROP INDEX CONCURRENTLY IF EXISTS public.idx_cnae_sec_covering;

-- 2802.5 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_estado_situacao_cnpj;

-- 2323.2 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_ddd1_covering;

-- 2096.0 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_email_hash;

-- 2077.5 MB · empresa
DROP INDEX CONCURRENTLY IF EXISTS public.idx_empresa_porte_cnpj;

-- 1795.7 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_email_prospeccao;

-- 1701.7 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_email;

-- 1609.7 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_bairro_trgm;

-- 1487.4 MB · simples
DROP INDEX CONCURRENTLY IF EXISTS public.idx_simples_opcoes;

-- 1482.1 MB · empresa
DROP INDEX CONCURRENTLY IF EXISTS public.idx_empresa_capital;

-- 1120.3 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_temporal;

-- 1076.7 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_cidade_ativas_cnpj;

-- 1076.7 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_regiao_ativas_cnpj;

-- 1073.5 MB · socio
DROP INDEX CONCURRENTLY IF EXISTS public.idx_socio_nome_trgm;

-- 1062.4 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_nome_fantasia;

-- 990.4 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_telefone;

-- 763.0 MB · simples
DROP INDEX CONCURRENTLY IF EXISTS public.idx_simples_opcao_simples;

-- 545.8 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_estado_cidade_cnae;

-- 521.0 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_cep;

-- 517.4 MB · simples
DROP INDEX CONCURRENTLY IF EXISTS public.idx_simples_opcao_mei;

-- 493.4 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_prospeccao;

-- 493.4 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_estado_cnae_situacao;

-- 489.9 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_estado_cnae;

-- 481.6 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_data_situacao;

-- 480.1 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_uf_municipio;

-- 480.1 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_municipio;

-- 478.0 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_regiao_estado;

-- 456.5 MB · empresa
DROP INDEX CONCURRENTLY IF EXISTS public.idx_empresa_natureza;

-- 456.4 MB · empresa
DROP INDEX CONCURRENTLY IF EXISTS public.idx_empresa_porte;

-- 340.1 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_leads_email;

-- 35.6 MB · mv_movimentacao_mensal_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_cnae_cnae;

-- 25.8 MB · mv_movimentacao_mensal_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_cnae_estado;

-- 25.5 MB · mv_movimentacao_mensal_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_cnae_regiao;

-- 11.0 MB · mv_abertura_periodo
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_abertura_regiao;

-- 6.9 MB · mv_comparativo_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_cnae_variacao;

-- 1.6 MB · mv_movimentacao_mensal_natureza
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_natureza_natureza;

-- 1.6 MB · estabelecimento
DROP INDEX CONCURRENTLY IF EXISTS public.idx_estab_data_inicio_brin;

-- 1.3 MB · mv_movimentacao_mensal_natureza
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_natureza_estado;

-- 1.3 MB · mv_comparativo_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_cnae_estado;

-- 1.3 MB · mv_comparativo_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_cnae_regiao;

-- 1.2 MB · mv_movimentacao_mensal_natureza
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_natureza_regiao;

-- 0.4 MB · mv_movimentacao_mensal_porte
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_porte_estado;

-- 0.2 MB · mv_movimentacao_mensal_porte
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_porte_regiao;

-- 0.2 MB · mv_comparativo_territorio
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_territorio_estado;

-- 0.2 MB · mv_movimentacao_mensal_porte
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_mov_porte_porte;

-- 0.2 MB · mv_comparativo_territorio
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_territorio_regiao;

-- 0.1 MB · ibge_cidade
DROP INDEX CONCURRENTLY IF EXISTS public.idx_ibge_cidade_municipio;

-- 0.1 MB · mv_stats_natureza_juridica_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_nj_cnae_total;

-- 0.1 MB · mv_comparativo_natureza
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_natureza_estado;

-- 0.1 MB · mv_comparativo_natureza
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_comp_natureza_regiao;

-- 0.1 MB · mv_stats_natureza_juridica
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_natureza_nome_trgm;

-- 0.1 MB · mv_stats_natureza_juridica_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_nj_est_regiao_total;

-- 0.0 MB · mv_stats_cnae
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_stats_cnae_total;

-- 0.0 MB · mv_stats_natureza_juridica_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_nj_est_ativos;

-- 0.0 MB · mv_stats_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_stats_estado_regiao;

-- 0.0 MB · mv_stats_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_stats_estado_sigla;

-- 0.0 MB · mv_stats_natureza_juridica
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_natureza_nome;

-- 0.0 MB · mv_stats_natureza_juridica
DROP INDEX CONCURRENTLY IF EXISTS public.idx_mv_natureza_ativos;

-- 0.0 MB · ibge_regiao
DROP INDEX CONCURRENTLY IF EXISTS public.idx_ibge_regiao_sigla;

-- 0.0 MB · ibge_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_ibge_estado_regiao;

-- 0.0 MB · ibge_estado
DROP INDEX CONCURRENTLY IF EXISTS public.idx_ibge_estado_sigla;

-- =============================================================================
-- Depois de rodar:
--   SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;   -- vazio
--   SELECT pg_size_pretty(pg_database_size(current_database()));      -- -35 GB
-- =============================================================================
