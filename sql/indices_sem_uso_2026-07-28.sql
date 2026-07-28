-- =============================================================================
-- DDL dos 67 índices com idx_scan = 0 em 28/07/2026
-- =============================================================================
-- ESTE ARQUIVO NÃO EXECUTA NADA. É o registro para RECRIAR qualquer índice que
-- venha a fazer falta depois do drop — e é a pré-condição do item 13 do roadmap
-- 20: "versione o DDL de todos os 67 num arquivo do cnpj-pipeline".
--
-- Coletado com pg_get_indexdef() no banco de produção. Soma: 36.8 GB
-- de 134 GB — 28% do banco.
--
-- RESSALVA METODOLÓGICA (01 §6.6), que vale mais que a lista:
--   `stats_reset` é NULL e o uptime era de 2 dias e 17 horas na coleta. Um
--   índice que só serve a um relatório MENSAL apareceria aqui como morto, e o
--   drop só doeria na próxima virada de mês. A contagem não distingue "nunca
--   usado" de "não usado desde o último restart".
--
--   O que reduz esse risco a um custo aceitável é este arquivo: recriar um
--   índice é `CREATE INDEX CONCURRENTLY` a partir das linhas abaixo.
-- =============================================================================

-- estabelecimento_cnae_sec.idx_cnae_sec_covering  ·  4750.1 MB  ·  a dropar
CREATE INDEX idx_cnae_sec_covering ON public.estabelecimento_cnae_sec USING btree (cod_cnae) INCLUDE (cnpj_completo);

-- estabelecimento.idx_estab_estado_situacao_cnpj  ·  2802.5 MB  ·  a dropar
CREATE INDEX idx_estab_estado_situacao_cnpj ON public.estabelecimento USING btree (cod_estado_ibge, cod_situacao_cadastral, cnpj_completo);

-- estabelecimento.idx_estab_ddd1_covering  ·  2323.2 MB  ·  a dropar
CREATE INDEX idx_estab_ddd1_covering ON public.estabelecimento USING btree (ddd_telefone_1) INCLUDE (cnpj_completo, cod_cidade_ibge) WHERE ((ddd_telefone_1 IS NOT NULL) AND ((ddd_telefone_1)::text <> ''::text));

-- estabelecimento.idx_estab_email_hash  ·  2096.0 MB  ·  a dropar
CREATE INDEX idx_estab_email_hash ON public.estabelecimento USING hash (email);

-- empresa.idx_empresa_porte_cnpj  ·  2077.5 MB  ·  a dropar
CREATE INDEX idx_empresa_porte_cnpj ON public.empresa USING btree (cod_porte, cnpj_basico);

-- empresa.empresa_pkey  ·  2077.5 MB  ·  MANTIDO — chave primária
CREATE UNIQUE INDEX empresa_pkey ON public.empresa USING btree (cnpj_basico);

-- estabelecimento.idx_estab_email_prospeccao  ·  1795.7 MB  ·  a dropar
CREATE INDEX idx_estab_email_prospeccao ON public.estabelecimento USING btree (cod_cidade_ibge, cnpj_completo) WHERE ((email IS NOT NULL) AND (email <> ''::text) AND (email !~~* '%contab%'::text));

-- estabelecimento.idx_estab_email  ·  1701.7 MB  ·  a dropar
CREATE INDEX idx_estab_email ON public.estabelecimento USING btree (email) WHERE ((email IS NOT NULL) AND (email <> ''::text));

-- estabelecimento.idx_estab_bairro_trgm  ·  1609.7 MB  ·  a dropar
CREATE INDEX idx_estab_bairro_trgm ON public.estabelecimento USING gin (bairro gin_trgm_ops);

-- simples.idx_simples_opcoes  ·  1487.4 MB  ·  a dropar
CREATE INDEX idx_simples_opcoes ON public.simples USING btree (opcao_simples, opcao_mei, cnpj_basico);

-- empresa.idx_empresa_capital  ·  1482.1 MB  ·  a dropar
CREATE INDEX idx_empresa_capital ON public.empresa USING btree (capital_social);

-- estabelecimento.idx_estab_temporal  ·  1120.3 MB  ·  a dropar
CREATE INDEX idx_estab_temporal ON public.estabelecimento USING btree (cod_estado_ibge, cod_cnae_principal, data_inicio_atividade);

-- estabelecimento.idx_estab_cidade_ativas_cnpj  ·  1076.7 MB  ·  a dropar
CREATE INDEX idx_estab_cidade_ativas_cnpj ON public.estabelecimento USING btree (cod_cidade_ibge, cnpj_completo) WHERE ((cod_situacao_cadastral)::text = '02'::text);

-- estabelecimento.idx_estab_regiao_ativas_cnpj  ·  1076.7 MB  ·  a dropar
CREATE INDEX idx_estab_regiao_ativas_cnpj ON public.estabelecimento USING btree (cod_regiao_ibge, cnpj_completo) WHERE ((cod_situacao_cadastral)::text = '02'::text);

-- socio.idx_socio_nome_trgm  ·  1073.5 MB  ·  a dropar
CREATE INDEX idx_socio_nome_trgm ON public.socio USING gin (nome_socio gin_trgm_ops);

-- estabelecimento.idx_estab_nome_fantasia  ·  1062.4 MB  ·  a dropar
CREATE INDEX idx_estab_nome_fantasia ON public.estabelecimento USING btree (nome_fantasia);

-- estabelecimento.idx_estab_telefone  ·  990.4 MB  ·  a dropar
CREATE INDEX idx_estab_telefone ON public.estabelecimento USING btree (telefone_1) WHERE ((telefone_1 IS NOT NULL) AND ((telefone_1)::text <> ''::text));

-- simples.idx_simples_opcao_simples  ·  763.0 MB  ·  a dropar
CREATE INDEX idx_simples_opcao_simples ON public.simples USING btree (cnpj_basico) WHERE ((opcao_simples)::text = 'S'::text);

-- estabelecimento.idx_estab_estado_cidade_cnae  ·  545.8 MB  ·  a dropar
CREATE INDEX idx_estab_estado_cidade_cnae ON public.estabelecimento USING btree (cod_estado_ibge, cod_cidade_ibge, cod_cnae_principal);

-- estabelecimento.idx_estab_cep  ·  521.0 MB  ·  a dropar
CREATE INDEX idx_estab_cep ON public.estabelecimento USING btree (cep);

-- simples.idx_simples_opcao_mei  ·  517.4 MB  ·  a dropar
CREATE INDEX idx_simples_opcao_mei ON public.simples USING btree (cnpj_basico) WHERE ((opcao_mei)::text = 'S'::text);

-- estabelecimento.idx_estab_prospeccao  ·  493.4 MB  ·  a dropar
CREATE INDEX idx_estab_prospeccao ON public.estabelecimento USING btree (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- estabelecimento.idx_estab_estado_cnae_situacao  ·  493.4 MB  ·  a dropar
CREATE INDEX idx_estab_estado_cnae_situacao ON public.estabelecimento USING btree (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- estabelecimento.idx_estab_estado_cnae  ·  489.9 MB  ·  a dropar
CREATE INDEX idx_estab_estado_cnae ON public.estabelecimento USING btree (cod_estado_ibge, cod_cnae_principal);

-- estabelecimento.idx_estab_data_situacao  ·  481.6 MB  ·  a dropar
CREATE INDEX idx_estab_data_situacao ON public.estabelecimento USING btree (data_situacao_cadastral);

-- estabelecimento.idx_estab_uf_municipio  ·  480.1 MB  ·  a dropar
CREATE INDEX idx_estab_uf_municipio ON public.estabelecimento USING btree (uf, cod_municipio);

-- estabelecimento.idx_estab_municipio  ·  480.1 MB  ·  a dropar
CREATE INDEX idx_estab_municipio ON public.estabelecimento USING btree (cod_municipio);

-- estabelecimento.idx_estab_regiao_estado  ·  478.0 MB  ·  a dropar
CREATE INDEX idx_estab_regiao_estado ON public.estabelecimento USING btree (cod_regiao_ibge, cod_estado_ibge);

-- empresa.idx_empresa_natureza  ·  456.5 MB  ·  a dropar
CREATE INDEX idx_empresa_natureza ON public.empresa USING btree (cod_natureza_juridica);

-- empresa.idx_empresa_porte  ·  456.4 MB  ·  a dropar
CREATE INDEX idx_empresa_porte ON public.empresa USING btree (cod_porte);

-- estabelecimento.idx_estab_leads_email  ·  340.1 MB  ·  a dropar
CREATE INDEX idx_estab_leads_email ON public.estabelecimento USING btree (cod_cnae_principal, cod_situacao_cadastral) WHERE ((email IS NOT NULL) AND (email <> ''::text));

-- mv_movimentacao_mensal_cnae.idx_mv_mov_cnae_cnae  ·  35.6 MB  ·  a dropar
CREATE INDEX idx_mv_mov_cnae_cnae ON public.mv_movimentacao_mensal_cnae USING btree (cod_cnae_principal, mes_abertura);

-- mv_movimentacao_mensal_cnae.idx_mv_mov_cnae_estado  ·  25.8 MB  ·  a dropar
CREATE INDEX idx_mv_mov_cnae_estado ON public.mv_movimentacao_mensal_cnae USING btree (cod_estado_ibge, mes_abertura);

-- mv_movimentacao_mensal_cnae.idx_mv_mov_cnae_regiao  ·  25.5 MB  ·  a dropar
CREATE INDEX idx_mv_mov_cnae_regiao ON public.mv_movimentacao_mensal_cnae USING btree (cod_regiao_ibge, mes_abertura);

-- mv_abertura_periodo.idx_mv_abertura_regiao  ·  11.0 MB  ·  a dropar
CREATE INDEX idx_mv_abertura_regiao ON public.mv_abertura_periodo USING btree (cod_regiao_ibge, mes_abertura);

-- mv_comparativo_cnae.idx_mv_comp_cnae_variacao  ·  6.9 MB  ·  a dropar
CREATE INDEX idx_mv_comp_cnae_variacao ON public.mv_comparativo_cnae USING btree (nivel, periodo_meses, aberturas_variacao_pct DESC);

-- mv_movimentacao_mensal_natureza.idx_mv_mov_natureza_natureza  ·  1.6 MB  ·  a dropar
CREATE INDEX idx_mv_mov_natureza_natureza ON public.mv_movimentacao_mensal_natureza USING btree (cod_natureza, mes_abertura);

-- estabelecimento.idx_estab_data_inicio_brin  ·  1.6 MB  ·  a dropar
CREATE INDEX idx_estab_data_inicio_brin ON public.estabelecimento USING brin (data_inicio_atividade) WITH (pages_per_range='32');

-- mv_movimentacao_mensal_natureza.idx_mv_mov_natureza_estado  ·  1.3 MB  ·  a dropar
CREATE INDEX idx_mv_mov_natureza_estado ON public.mv_movimentacao_mensal_natureza USING btree (cod_estado_ibge, mes_abertura);

-- mv_comparativo_cnae.idx_mv_comp_cnae_estado  ·  1.3 MB  ·  a dropar
CREATE INDEX idx_mv_comp_cnae_estado ON public.mv_comparativo_cnae USING btree (cod_estado_ibge, periodo_meses);

-- mv_comparativo_cnae.idx_mv_comp_cnae_regiao  ·  1.3 MB  ·  a dropar
CREATE INDEX idx_mv_comp_cnae_regiao ON public.mv_comparativo_cnae USING btree (cod_regiao_ibge, periodo_meses);

-- mv_movimentacao_mensal_natureza.idx_mv_mov_natureza_regiao  ·  1.2 MB  ·  a dropar
CREATE INDEX idx_mv_mov_natureza_regiao ON public.mv_movimentacao_mensal_natureza USING btree (cod_regiao_ibge, mes_abertura);

-- mv_movimentacao_mensal_porte.idx_mv_mov_porte_pk  ·  0.8 MB  ·  MANTIDO — índice único de MV — REFRESH CONCURRENTLY depende dele
CREATE UNIQUE INDEX idx_mv_mov_porte_pk ON public.mv_movimentacao_mensal_porte USING btree (mes_abertura, cod_porte, cod_estado_ibge);

-- mv_movimentacao_mensal_porte.idx_mv_mov_porte_estado  ·  0.4 MB  ·  a dropar
CREATE INDEX idx_mv_mov_porte_estado ON public.mv_movimentacao_mensal_porte USING btree (cod_estado_ibge, mes_abertura);

-- mv_stats_natureza_juridica_cnae.idx_mv_nj_cnae_pk  ·  0.4 MB  ·  MANTIDO — índice único de MV — REFRESH CONCURRENTLY depende dele
CREATE UNIQUE INDEX idx_mv_nj_cnae_pk ON public.mv_stats_natureza_juridica_cnae USING btree (cod_natureza, cod_cnae);

-- mv_movimentacao_mensal_porte.idx_mv_mov_porte_regiao  ·  0.2 MB  ·  a dropar
CREATE INDEX idx_mv_mov_porte_regiao ON public.mv_movimentacao_mensal_porte USING btree (cod_regiao_ibge, mes_abertura);

-- mv_comparativo_territorio.idx_mv_comp_territorio_estado  ·  0.2 MB  ·  a dropar
CREATE INDEX idx_mv_comp_territorio_estado ON public.mv_comparativo_territorio USING btree (cod_estado_ibge, periodo_meses);

-- mv_movimentacao_mensal_porte.idx_mv_mov_porte_porte  ·  0.2 MB  ·  a dropar
CREATE INDEX idx_mv_mov_porte_porte ON public.mv_movimentacao_mensal_porte USING btree (cod_porte, mes_abertura);

-- mv_comparativo_territorio.idx_mv_comp_territorio_regiao  ·  0.2 MB  ·  a dropar
CREATE INDEX idx_mv_comp_territorio_regiao ON public.mv_comparativo_territorio USING btree (cod_regiao_ibge, periodo_meses);

-- ibge_cidade.idx_ibge_cidade_municipio  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_ibge_cidade_municipio ON public.ibge_cidade USING btree (cod_municipio);

-- mv_stats_natureza_juridica_cnae.idx_mv_nj_cnae_total  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_mv_nj_cnae_total ON public.mv_stats_natureza_juridica_cnae USING btree (total DESC);

-- mv_comparativo_natureza.idx_mv_comp_natureza_estado  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_mv_comp_natureza_estado ON public.mv_comparativo_natureza USING btree (cod_estado_ibge, periodo_meses);

-- mv_comparativo_natureza.idx_mv_comp_natureza_regiao  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_mv_comp_natureza_regiao ON public.mv_comparativo_natureza USING btree (cod_regiao_ibge, periodo_meses);

-- mv_stats_natureza_juridica.idx_mv_natureza_nome_trgm  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_mv_natureza_nome_trgm ON public.mv_stats_natureza_juridica USING gin (nome_natureza gin_trgm_ops);

-- mv_stats_natureza_juridica_estado.idx_mv_nj_est_regiao_total  ·  0.1 MB  ·  a dropar
CREATE INDEX idx_mv_nj_est_regiao_total ON public.mv_stats_natureza_juridica_estado USING btree (cod_regiao_ibge, total DESC);

-- mv_stats_cnae.idx_mv_stats_cnae_total  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_stats_cnae_total ON public.mv_stats_cnae USING btree (total_estabelecimentos DESC);

-- mv_stats_natureza_juridica_estado.idx_mv_nj_est_ativos  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_nj_est_ativos ON public.mv_stats_natureza_juridica_estado USING btree (ativos DESC);

-- mv_stats_estado.idx_mv_stats_estado_pk  ·  0.0 MB  ·  MANTIDO — índice único de MV — REFRESH CONCURRENTLY depende dele
CREATE UNIQUE INDEX idx_mv_stats_estado_pk ON public.mv_stats_estado USING btree (cod_estado_ibge);

-- mv_stats_estado.idx_mv_stats_estado_regiao  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_stats_estado_regiao ON public.mv_stats_estado USING btree (cod_regiao_ibge);

-- mv_stats_estado.idx_mv_stats_estado_sigla  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_stats_estado_sigla ON public.mv_stats_estado USING btree (sigla_uf);

-- mv_stats_natureza_juridica.idx_mv_natureza_nome  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_natureza_nome ON public.mv_stats_natureza_juridica USING btree (nome_natureza);

-- mv_stats_natureza_juridica.idx_mv_natureza_ativos  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_mv_natureza_ativos ON public.mv_stats_natureza_juridica USING btree (ativos DESC);

-- ibge_regiao.ibge_regiao_sigla_regiao_key  ·  0.0 MB  ·  MANTIDO — sustenta constraint UNIQUE
CREATE UNIQUE INDEX ibge_regiao_sigla_regiao_key ON public.ibge_regiao USING btree (sigla_regiao);

-- ibge_regiao.idx_ibge_regiao_sigla  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_ibge_regiao_sigla ON public.ibge_regiao USING btree (sigla_regiao);

-- ibge_estado.idx_ibge_estado_regiao  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_ibge_estado_regiao ON public.ibge_estado USING btree (cod_regiao_ibge);

-- ibge_estado.idx_ibge_estado_sigla  ·  0.0 MB  ·  a dropar
CREATE INDEX idx_ibge_estado_sigla ON public.ibge_estado USING btree (sigla_uf);

-- ibge_estado.ibge_estado_sigla_uf_key  ·  0.0 MB  ·  MANTIDO — sustenta constraint UNIQUE
CREATE UNIQUE INDEX ibge_estado_sigla_uf_key ON public.ibge_estado USING btree (sigla_uf);

