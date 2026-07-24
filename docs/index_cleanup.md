# Limpeza de índices redundantes (AG9 — 2026-07)

A tabela `estabelecimento` chegou a **43 GB de índices para 15 GB de dados**.
Como o banco usa collation `C`, os índices `varchar_pattern_ops` são
duplicatas exatas dos btree comuns (que já atendem `LIKE 'prefixo%'`), e
vários índices de coluna única são prefixos exatos de índices compostos
existentes. Este documento registra o que foi removido das definições do ETL,
por quê, e o protocolo para remover em produção com segurança.

Ganho estimado: **~20 GB** de disco/cache liberados e carga mensal mais curta.

## Removidos das definições (valem a partir da próxima carga completa)

### Leva 1 — duplicatas exatas (risco zero: outro índice dá a mesma capacidade)

| Índice | Tamanho | Motivo |
|---|---|---|
| `idx_empresa_razao_social_prefix` | 3,7 GB | `varchar_pattern_ops` duplica `idx_empresa_razao_social` (collation `C`) |
| `idx_estab_nome_fantasia_prefix` | 1,0 GB | duplica `idx_estab_nome_fantasia` |
| `idx_socio_nome_prefix` | 0,8 GB | duplica `idx_socio_nome` |
| `idx_estab_cnpj_completo_hash` | 2,0 GB | duplica a PK btree para igualdade |

### Leva 2 — prefixos de compostos / baixa seletividade (exigem verificação de uso)

| Índice | Tamanho | Motivo |
|---|---|---|
| `idx_estab_estado_ibge` | 0,5 GB | prefixo de `idx_estab_estado_situacao_cnpj`, `idx_estab_estado_matriz_cnpj` etc. |
| `idx_estab_cidade_ibge` | 0,5 GB | prefixo de `idx_estab_cidade_situacao_cnpj` |
| `idx_estab_regiao_ibge` | 0,5 GB | prefixo de `idx_estab_regiao_estado` |
| `idx_estab_cnae_principal` | 0,5 GB | prefixo de `idx_estab_cnae_cidade`/`idx_estab_cnae_estado` |
| `idx_estab_situacao` | 0,5 GB | seletividade baixa (75% da base é "ativa"); parciais `idx_estab_ativas` cobrem o caso útil |
| `idx_estab_matriz_filial` | 0,5 GB | cardinalidade 2 — inútil isolado |
| `idx_estab_ddd` | 0,5 GB | coberto por `idx_estab_ddd1_covering` (parcial + covering) |
| `idx_estab_data_inicio` | 0,5 GB | coberto por `idx_estab_novos_estado` + `idx_estab_data_inicio_brin` |

## Mantido — remoção condicional

| Índice | Tamanho | Condição |
|---|---|---|
| `idx_empresa_razao_social` | 3,7 GB | **Manter.** O website ainda ordena por `razao_social` (`sort=corporate_name` da API de busca). Só remover quando nenhum `ORDER BY razao_social` sobreviver no código (previsto pós-AG14, quando a busca migrar para a tabela `busca_estabelecimento`). |

## Protocolo de remoção em produção (tarefa F6)

A remoção no banco **atual** (sem recarga) usa o script
[`sql/prod_hygiene.sql`](../sql/prod_hygiene.sql), que dropa **apenas a
leva 1** (duplicatas exatas — o índice gêmeo garante a mesma capacidade).

Para a **leva 2**, o protocolo é obrigatório porque outros consumidores
(`sitemap-service`, painéis, consultas ad hoc) podem usar índices que o
mapeamento do website não enxerga:

1. Pré-requisito: `pg_stat_statements` ativo (`shared_preload_libraries`,
   implantado via imagem do `infra/` — tarefa F4) e estatísticas zeradas em
   data conhecida (`SELECT pg_stat_reset();` opcional, anotar a data).
2. Observar por **30 dias**:

   ```sql
   SELECT relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
   FROM pg_stat_user_indexes
   WHERE indexrelname IN (
       'idx_estab_estado_ibge', 'idx_estab_cidade_ibge', 'idx_estab_regiao_ibge',
       'idx_estab_cnae_principal', 'idx_estab_situacao', 'idx_estab_matriz_filial',
       'idx_estab_ddd', 'idx_estab_data_inicio'
   )
   ORDER BY idx_scan DESC;
   ```

3. Só dropar (com `DROP INDEX CONCURRENTLY`) os índices com `idx_scan ≈ 0`
   no período. Qualquer índice com uso real: investigar o consumidor antes
   (frequentemente a query dele é atendida por um composto existente — nesse
   caso, validar com `EXPLAIN` e então dropar).
4. Rollback: o DDL de recriação de cada índice está comentado no script de
   higiene (`CREATE INDEX CONCURRENTLY` — sem lock de escrita).

## Efeito nas próximas cargas

As definições removidas deixam de ser criadas pelo `python etl.py db index`
a partir deste commit — a carga mensal cria o conjunto enxuto. Nenhuma
mudança é aplicada automaticamente a bancos já existentes: para o banco de
produção atual, use o script de higiene (F6).
