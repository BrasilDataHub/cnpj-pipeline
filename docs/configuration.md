# Configuração e Personalização

## Constantes Globais

Todas as **constantes globais** como diretórios, downloads simultâneos, entre outras, podem ser ajustadas em
`src/rfb_cnpj_etl/config.py`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DOWNLOAD_DIR` | Diretório de downloads | `data/downloads` |
| `DOWNLOAD_MAX_CONCURRENTS` | Downloads simultâneos | `10` |
| `POSTGRES` | Credenciais do PostgreSQL | `localhost:5432` |
| `BATCH_SIZE` | Tamanho do lote de inserção | `250000` |

## Variáveis de Ambiente

As configurações também podem ser definidas via variáveis de ambiente:

| Variável | Descrição |
|----------|-----------|
| `POSTGRES_HOST` | Host do PostgreSQL |
| `POSTGRES_PORT` | Porta do PostgreSQL |
| `POSTGRES_USER` | Usuário do PostgreSQL |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL |
| `POSTGRES_DBNAME` | Nome do banco de dados |
| `DOWNLOAD_PATH` | Diretório para downloads |
| `IBGE_CSV_DIR` | Diretório dos CSVs do IBGE |
| `LOG_FILE` | Caminho do arquivo de log (arquivo ou diretório) |

## Chaves Primárias, Estrangeiras e Índices

As definições de chaves primárias, estrangeiras e índices podem ser encontradas em `src/rfb_cnpj_etl/db/schema.py`.
Edite conforme a sua necessidade.

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

### Configuração via variável de ambiente

Defina `LOG_FILE` para sobrescrever o caminho padrão. Exemplos:

```bash
# Diretório (gera etl-YYYY-MM-DD.log dentro dele)
export LOG_FILE=data/logs/

# Arquivo (insere a data antes da extensão)
export LOG_FILE=data/logs/etl.log

# Com placeholder de data
export LOG_FILE=data/logs/etl-{date}.log
```

### Configuração via CLI

Use `--log-file` para sobrescrever a variável de ambiente:

```bash
python etl.py complete --log-file /var/log/etl/etl-{date}.log
```

---

## Materialized Views (Opcionais)

As Materialized Views pré-calculam estatísticas agregadas, reduzindo consultas de minutos para milissegundos.

### Criação via CLI (Recomendado)

```bash
# Criar/recriar todas as Materialized Views (ou após usar --skip-views no complete)
python etl.py db views create

# Atualizar após nova carga de dados
python etl.py db views refresh --concurrent
```

### Views Disponíveis

| View | Descrição |
|------|-----------|
| `mv_stats_estado` | Estatísticas por estado |
| `mv_stats_municipio` | Estatísticas por município |
| `mv_stats_cnae` | Estatísticas por CNAE |
| `mv_stats_cnae_estado` | Estatísticas CNAE x Estado |
| `mv_abertura_periodo` | Aberturas por período |
| `mv_top_cnaes_cidade` | Top CNAEs por cidade |
| `mv_stats_natureza_juridica_estado` | Estatísticas por natureza jurídica x estado |
| `mv_stats_natureza_juridica_municipio` | Estatísticas por natureza jurídica x município |
| `mv_stats_natureza_juridica` | Estatísticas por natureza jurídica |

Os scripts SQL estão em `sql/materialized_views/` e são executados na ordem alfabética pelo CLI.

---

## Scripts SQL Auxiliares (Opcionais)

Na pasta `sql/` estão disponíveis **scripts auxiliares** para otimizações avançadas. Esses scripts **não são executados
automaticamente** pelo ETL e devem ser aplicados manualmente conforme a necessidade do seu ambiente.

> **Nota:** Todos os índices (básicos e avançados como GIN, BRIN, HASH) já são criados automaticamente
> pelo comando `db index`. Os scripts abaixo oferecem otimizações adicionais para cenários específicos.

### Quando Utilizar

Execute esses scripts **após a conclusão do ETL** (após `db fk` ou `complete`), quando:
- Necessitar de funções de manutenção e validação

### Scripts Disponíveis

| Arquivo | Propósito | Pré-requisitos |
|---------|-----------|----------------|
| `general_improvements.sql` | Extensões PostgreSQL, funções de manutenção, validações e configurações de performance | Permissões de superusuário para algumas operações |

### Como Executar

```bash
# Conectar ao banco e executar (substitua as credenciais)
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/general_improvements.sql
```

### Detalhes

**`general_improvements.sql`** - Configurações e manutenção:
- Extensões: `pg_trgm`, `pg_prewarm`, `pg_stat_statements`, `unaccent`
- Funções: `prewarm_critical_indexes()`, `vacuum_analyze_all()`, `table_statistics()`
- Validações: `validate_cnpj_completo()`, `check_referential_integrity()`
