# Configuração e Personalização

Este documento é a **referência canônica** de variáveis de ambiente e constantes do
pipeline. As demais páginas (README, guia Docker, observabilidade) apontam para cá.

## Variáveis de Ambiente

Todas as variáveis podem ser definidas no ambiente ou no arquivo `.env` na raiz do
projeto (valores já presentes no ambiente têm prioridade sobre o `.env`). Copie o
modelo com `cp .env.example .env`.

### Conexão com o PostgreSQL

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `POSTGRES_HOST` | Host do PostgreSQL | `localhost` |
| `POSTGRES_PORT` | Porta do PostgreSQL | `5432` |
| `POSTGRES_USER` | Usuário do PostgreSQL | `postgres` |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL | `sua_senha_aqui` |
| `POSTGRES_DBNAME` | Nome do banco de dados | `dados_cnpj` |

### Diretórios e origem dos dados

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DOWNLOAD_PATH` | Diretório para os ZIPs baixados | `data/downloads` |
| `IBGE_CSV_DIR` | Diretório dos CSVs do IBGE (regiões/estados/cidades) | `data/locations` |
| `PIPELINE_STATE_DIR` | Diretório do estado de execução (checkpoint/retomada) | `data/state` |
| `PIPELINE_DEAD_LETTER_DIR` | Diretório onde lotes de COPY que falharam definitivamente são preservados para reprocessamento (`db dead-letter --retry`) | `data/logs/dead_letter` |
| `RFB_WEBDAV_URL` | URL base WebDAV (Nextcloud) da Receita Federal — altere se o token público mudar | URL oficial da RFB |
| `LOG_FILE` | Caminho do arquivo de log (arquivo, diretório ou padrão com `{date}`) | `data/logs/etl-<AAAA-MM-DD>.log` |

Caminhos relativos são resolvidos contra a raiz do projeto.

### Observabilidade (estado, dashboard, webhooks)

Guia completo: [Observabilidade](observabilidade.md).

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PIPELINE_WEBHOOK_URL` | URL para notificações HTTP por etapa (opt-in) | *(desligado)* |
| `PIPELINE_PORT` | Porta do dashboard (`--serve`) | `3010` |
| `PIPELINE_REFRESH_SECONDS` | Intervalo inicial de atualização da página do dashboard | `6` |
| `PIPELINE_DASHBOARD_USER` | Usuário do Basic Auth do dashboard | `pipeline` |
| `PIPELINE_DASHBOARD_PASSWORD` | Senha do Basic Auth; vazia = uma é gerada e exibida no log a cada execução | *(gerada)* |
| `PIPELINE_MAX_ATTEMPTS` | Tentativas por etapa antes de exigir intervenção (`0` = ilimitado) | `3` |

### Criação de índices

O banco de carga nunca tem usuários durante o pipeline (o site só troca a conexão
depois da conclusão), então os índices são criados **sem** `CONCURRENTLY` e em
paralelo — o objetivo é o menor tempo total.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `INDEX_MAX_WORKERS` | Conexões simultâneas na criação de índices | `4` |
| `INDEX_MAINTENANCE_WORK_MEM` | `maintenance_work_mem` aplicado em cada conexão de worker | `2GB` |

> **Memória**: o pico é aproximadamente `INDEX_MAX_WORKERS × INDEX_MAINTENANCE_WORK_MEM`
> (com o padrão, até ~8 GB do lado do Postgres). Em hosts com 8–16 GB de RAM,
> reduza um dos dois (ex.: `INDEX_MAINTENANCE_WORK_MEM=512MB`).

### Build das Materialized Views

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `MV_BUILD_WORK_MEM` | `work_mem` da sessão que constrói as MVs | `1GB` |
| `MV_BUILD_MAINTENANCE_WORK_MEM` | `maintenance_work_mem` da mesma sessão | `2GB` |

Os dois valem **só para a sessão de build** — o resto do banco continua com o
que está no `postgresql.conf`.

### Ciclo mensal blue/green

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `MAX_DELTA_PCT` | Teto do gate de delta, em por cento. Acima dele o `db validate` reprova e **nada é publicado** | `25` |
| `PIPELINE_LOCK_FILE` | `flock` advisory compartilhado com `sitemap-service` e `search-indexer-service`. Precisa ser o **mesmo caminho** nos três | `/var/lib/bdh/pipeline.lock` |

O diretório do lock precisa existir e ser gravável antes da primeira execução:

```bash
sudo mkdir -p /var/lib/bdh && sudo chown <usuario-do-pipeline> /var/lib/bdh
```

Guia completo: [Ciclo mensal blue/green](ciclo-blue-green.md).

### Somente docker-compose

Estas variáveis são lidas pelo `docker-compose.yaml`, nunca pelo Python:
`FORWARD_DB_PORT`, `FORWARD_DASHBOARD_PORT`, `IMAGE_TAG`, `DADOS_READ_PASSWORD`,
`ETL_UID`/`ETL_GID` e o tuning do Postgres (`PG_SHARED_BUFFERS`,
`PG_EFFECTIVE_CACHE_SIZE`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`,
`PG_MAX_WAL_SIZE`, `PG_RANDOM_PAGE_COST`, `PG_SHM_SIZE`, `PG_MEMORY_LIMIT`).
Detalhes no [Guia Docker](docker.md).

## Constantes Globais

Constantes ajustáveis apenas editando `src/rfb_cnpj_etl/config.py`:

| Constante | Descrição | Padrão |
|-----------|-----------|--------|
| `DEFAULT_PARALLEL` | Paralelismo da carga (`--parallel`) | `True` |
| `DEFAULT_LOW_MEMORY` | Modo de memória reduzida (`--low-memory`) | `False` |
| `BATCH_SIZE` | Registros por lote de COPY | `250_000` |
| `BATCH_RATIO` | Multiplicador do lote por tabela (`estabelecimento` usa lotes menores por ter linhas largas) | `{"estabelecimento": 0.4}` |
| `WORKER_THREADS` | Threads consumidoras da carga | `CPUs - 1` |
| `QUEUE_SIZE` | Tamanho da fila de inserção (back-pressure) | `max(4, 2×threads)` |
| `AVG_COMPRESSED_LINE_SIZE_BYTES` | Heurística bytes/linha para estimar o total de registros (só alimenta a barra de progresso) | `35` |
| `BRAZIL_COUNTRY_CODES` | Códigos RFB que representam Brasil no enriquecimento IBGE | `{"105", "0105"}` |
| `DOWNLOAD_CHUNK_SIZE` | Tamanho do chunk de download (bytes) | `8_192` |
| `DOWNLOAD_CHUNK_TIMEOUT` | Timeout por requisição de chunk (s) | `60` |
| `DOWNLOAD_MAX_RETRIES` | Tentativas por arquivo antes de falhar | `100` |
| `DOWNLOAD_MAX_CONCURRENTS` | Downloads simultâneos padrão (`--workers`) | `10` |
| `DEBUG_LOG` | `True` troca a barra de progresso por linhas de log detalhadas | `False` |
| `PIPELINE_STATS_TABLE` | Nome da tabela de estatísticas por execução | `pipeline_stats` |

## Chaves Primárias, Estrangeiras e Índices

As definições de tabelas, chaves primárias, estrangeiras e índices básicos ficam em
`src/rfb_cnpj_etl/db/schema.py`; os índices avançados (GIN, BRIN, HASH, parciais,
compostos) em `src/rfb_cnpj_etl/db/advanced_indexes.py`. Edite conforme a sua
necessidade. O catálogo completo está em [Banco de Dados](database.md).

---

## Logs e Auditoria

Por padrão, o CLI grava logs em `data/logs/etl-YYYY-MM-DD.log` (rotação diária simples).
A gravação é **sempre ativa** — não existe flag para desligá-la — e o arquivo é
aberto em modo append com flush por linha, então o conteúdo pode ser
acompanhado em tempo real enquanto o pipeline roda:

```bash
tail -f data/logs/etl-$(date +%F).log      # via Docker o container roda em UTC: use `date -u +%F`
```

É por esse arquivo que se acompanha uma execução em segundo plano (veja
[Guia Docker](docker.md#execução-em-segundo-plano-detached)). Via Docker, ele fica
no host graças ao volume `./data/logs:/app/data/logs` e sobrevive à remoção do
container.

O arquivo recebe apenas as **mensagens de etapa** (com horário e tempo
decorrido). As barras de progresso do `tqdm` vão só para o terminal — elas se
redesenham com `\r` e não fariam sentido em arquivo.

### Formas aceitas em `LOG_FILE`/`--log-file`

| Valor | Resultado |
|-------|-----------|
| *(vazio)* | `data/logs/etl-<AAAA-MM-DD>.log` |
| Diretório (`data/logs/`) | `<dir>/etl-<AAAA-MM-DD>.log` |
| Arquivo com extensão (`etl.log`) | `etl-<AAAA-MM-DD>.log` (data antes da extensão) |
| Nome sem extensão (`etl`) | `etl-<AAAA-MM-DD>.log` |
| Com placeholder (`etl-{date}.log`) | `{date}` substituído por `AAAA-MM-DD` |

Sempre há carimbo de data no nome — não existe modo "arquivo único".

```bash
# Via variável de ambiente
export LOG_FILE=data/logs/etl-{date}.log

# Via CLI (tem prioridade sobre a variável; vem ANTES do subcomando)
python etl.py --log-file /var/log/etl/etl-{date}.log complete
```

---

## Materialized Views

As Materialized Views pré-calculam estatísticas agregadas, reduzindo consultas de
minutos para milissegundos. Elas são criadas **automaticamente ao final do
`complete`** (a menos que se use `--skip-views`) e podem ser gerenciadas de forma
avulsa:

```bash
# Criar/recriar todas as Materialized Views (ou após usar --skip-views no complete)
python etl.py db views create

# Atualizar após nova carga de dados
python etl.py db views refresh --concurrent
```

### Views Disponíveis (13)

| View | Descrição |
|------|-----------|
| `mv_stats_estado` | Estatísticas por estado |
| `mv_stats_municipio` | Estatísticas por município |
| `mv_stats_cnae` | Estatísticas por CNAE |
| `mv_stats_cnae_estado` | Estatísticas CNAE × estado |
| `mv_abertura_periodo` | Aberturas por período |
| `mv_top_cnaes_cidade` | Top CNAEs por cidade |
| `mv_stats_cidade_situacao` | Estatísticas por cidade × situação cadastral |
| `mv_regime_tributario_cidade` | Regime tributário por cidade |
| `mv_porte_cidade` | Porte de empresa por cidade |
| `mv_stats_natureza_juridica_estado` | Natureza jurídica × estado |
| `mv_stats_natureza_juridica_municipio` | Natureza jurídica × município |
| `mv_stats_natureza_juridica` | Estatísticas por natureza jurídica |
| `mv_stats_natureza_juridica_cnae` | Natureza jurídica × CNAE |

Os scripts SQL estão em `sql/materialized_views/` e são executados na ordem
alfabética pelo CLI. A função `refresh_all_mvs()` (arquivo `99_refresh_function.sql`)
permite atualizar tudo direto no banco.

> **Atenção**: `db init` e `db load` recriam o schema com `DROP TABLE ... CASCADE`,
> o que **destrói as Materialized Views e a tabela de busca** — elas são recriadas
> nas etapas finais do `complete`. Detalhes em [Banco de Dados](database.md).

---

## Scripts SQL Auxiliares (Opcionais)

Na pasta `sql/` estão disponíveis **scripts auxiliares** para otimizações avançadas. Esses scripts **não são executados
automaticamente** pelo ETL e devem ser aplicados manualmente conforme a necessidade do seu ambiente.

> **Nota:** Todos os índices (básicos e avançados como GIN, BRIN, HASH) já são criados automaticamente
> pelo comando `db index` (e pelo `db load`/`complete` sem `--skip-index`). Os scripts abaixo oferecem
> otimizações adicionais para cenários específicos.

### Scripts Disponíveis

| Arquivo | Propósito | Observações |
|---------|-----------|-------------|
| `general_improvements.sql` | Extensões PostgreSQL, funções de manutenção, validações e configurações de performance | Permissões de superusuário para algumas operações |
| `prod_hygiene.sql` | Higiene de índices em produção (remoções documentadas em [index_cleanup](index_cleanup.md)) | Aplicar com critério, seguindo o protocolo do documento |
| `sitemap_indexes.sql` | Índices para geração de sitemaps | **Redundante** desde que os 3 índices foram incorporados a `advanced_indexes.py`; mantido apenas para referência |
| `roles_e_work_mem.sql` | `work_mem` e `statement_timeout` por role (item 32) | Idempotente. **Aplicar uma vez**, antes de subir `PG_WORK_MEM` no perfil da máquina |
| `indices_sem_uso_2026-07-28.sql` | DDL versionado dos 67 índices sem uso (item 13) | Só o `CREATE INDEX` de cada um: é o que permite desfazer um drop |
| `drop_indices_sem_uso.sql` | Remove os índices sem uso, liberando 34,8 GB de 134 GB | `DROP ... CONCURRENTLY`, portanto **fora de transação**: rode com `psql -f`, nunca colado num bloco. Exige o backup do item 0 funcionando |

**`roles_e_work_mem.sql`** é o par indivisível de `PG_WORK_MEM=96MB` no perfil
`compartilhada-14gb`. O valor alto existe porque o ETL e o build das 19 MVs
derramam em disco (9.545 derrames medidos, ~706 GB de IO evitável), mas o mesmo
96 MB numa consulta do site é `work_mem` **por operação de sort/hash** — com
paralelismo, quase 1 GB numa requisição anônima. O script dá a cada role o valor
que faz sentido para ela:

| Role | `work_mem` | `statement_timeout` | Superfície |
|------|-----------|---------------------|------------|
| `dados_read` | 32 MB | 5 s (+ `idle_in_transaction` 60 s) | o site |
| `dados_export` | 64 MB | 30 min (+ paralelismo limitado a 1) | exportações e sitemap |
| a role da carga | herda `PG_WORK_MEM` (96 MB) | sem teto | o pipeline |

O paralelismo do `dados_export` é limitado a 1 de propósito: a exportação pode
esperar, o visitante não.

Subir `PG_WORK_MEM` **sem** aplicar este script é o caminho para o site alocar
memória de ETL numa página pública.

### Como Executar

```bash
# Conectar ao banco e executar (substitua as credenciais)
psql -h localhost -U postgres -d dados_cnpj -f sql/general_improvements.sql
```

### Detalhes

**`general_improvements.sql`** - Configurações e manutenção:
- Extensões: `pg_trgm`, `pg_prewarm`, `pg_stat_statements`, `unaccent`
- Funções: `prewarm_critical_indexes()`, `vacuum_analyze_all()`, `table_statistics()`
- Validações: `validate_cnpj_completo()`, `check_referential_integrity()`
