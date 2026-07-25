# Referência de Comandos

## Visão Geral

```bash
# Execução local
python etl.py <comando> [opções]

# Execução via Docker (primeiro plano — prende o terminal)
docker compose run --rm etl <comando> [opções]

# Execução via Docker em segundo plano (comandos longos, como `complete`)
docker compose run -d --name cnpj-run etl <comando> [opções]
tail -f data/logs/etl-$(date +%F).log
```

| Comando | Descrição |
|---------|-----------|
| `get-availables` | Lista meses disponíveis no site da RFB |
| `get-latest` | Retorna o mês mais recente disponível |
| `get-urls` | Exibe URLs de download para um mês |
| `download` | Baixa arquivos ZIP da RFB |
| `db init` | Cria schema e tabelas no banco |
| `db load` | Carrega dados dos arquivos ZIP |
| `db patch` | Aplica correções estáticas na base |
| `db pk` | Adiciona chaves primárias |
| `db index` | Cria todos os índices (básicos + avançados) |
| `db fk` | Cria chaves estrangeiras |
| `db search` | Constrói/reconstrói a tabela de busca `busca_estabelecimento` (build-and-swap) |
| `db views create` | Cria/recria Materialized Views |
| `db views refresh` | Atualiza dados das Materialized Views |
| `complete` | Executa todo o pipeline (download + carga + views) |

---

## Opções Globais

Estas opções funcionam com **todos** os comandos.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--log-file` | `path` | `data/logs/etl-YYYY-MM-DD.log` | Arquivo de log (append) com rotação diária |

**Rotação simples por data**
- Se o caminho for um diretório (ou terminar com `/`), o arquivo será `etl-YYYY-MM-DD.log`.
- Se o caminho for um arquivo, a data será inserida antes da extensão.
- Use `{date}` para controlar o formato: `--log-file data/logs/etl-{date}.log`.

**Via variável de ambiente**
- Defina `LOG_FILE` para um caminho de arquivo ou diretório.
- `--log-file` sempre tem prioridade sobre `LOG_FILE`.

---

## Comandos de Consulta

```bash
# Lista todos os meses disponíveis
python etl.py get-availables

# Retorna o mês mais recente
python etl.py get-latest

# Exibe URLs de download para um mês
python etl.py get-urls --month 07/2026
```

---

## Comando `download`

Baixa os arquivos ZIP de dados abertos do CNPJ diretamente do site da Receita Federal.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--month` | `MM/AAAA` | Último mês | Mês para baixar |
| `--download-dir` | `path` | `data/downloads` | Diretório de destino |
| `--workers` | `int` | `10` | Downloads simultâneos |
| `--clean` | flag | - | Remove arquivos existentes antes de baixar |

```bash
# Baixar mês mais recente
python etl.py download

# Baixar mês específico
python etl.py download --month 07/2026

# Baixar com limpeza prévia e 4 workers
python etl.py download --month 07/2026 --clean --workers 4
```

---

## Comando `db init`

Cria o schema e as tabelas no banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db init
```

---

## Comando `db load`

Carrega os dados dos arquivos ZIP para o banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--month` | `MM/AAAA` | Último mês | Mês a ser carregado |
| `--download-dir` | `path` | `data/downloads/YYYY-MM` | Pasta com os arquivos ZIP |
| `--skip-index` | flag | - | Não cria índices ao final |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection frequente |
| `--parallel` | flag | - | Usa multi-threading na carga |
| `--only-data` | flag | - | Carrega apenas dados (sem patch/pk/index/fk) |

```bash
# Carga completa padrão (inclui todos os índices)
python etl.py db load --month 07/2026

# Carga apenas dados (sem extras)
python etl.py db load --month 07/2026 --only-data

# Carga com paralelismo
python etl.py db load --month 07/2026 --parallel
```

---

## Comandos `db patch`, `db pk`, `db index`, `db fk`

Executam etapas específicas do processo de carga.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db patch    # Aplica correções estáticas
python etl.py db pk       # Adiciona chaves primárias
python etl.py db index    # Cria todos os índices (básicos + avançados)
python etl.py db fk       # Cria chaves estrangeiras
```

O comando `db index` cria automaticamente:
- **Índices básicos**: BTREE simples para JOINs, FKs e consultas comuns (~25 índices)
- **Índices avançados** (~29 índices):
  - **GIN (pg_trgm)**: Busca textual com `LIKE '%termo%'` em nome fantasia, razão social e nome de sócios
  - **BRIN**: Índices compactos para colunas de data (economia de ~95% de espaço)
  - **HASH**: Lookups ultra-rápidos para CNPJ e email
  - **Parciais**: Índices apenas para empresas ativas ou com email preenchido
  - **Compostos**: Otimizados para consultas de prospecção, filtros por localização e CNAE

---

## Comando `db search`

Constrói (ou reconstrói) a **tabela de busca enxuta** `busca_estabelecimento`:
uma linha por estabelecimento, apenas os campos filtráveis da busca do website
e nomes normalizados com `unaccent(upper(...))` (razão social, nome fantasia
e bairro). É a etapa 6.5 do pipeline de carga — roda automaticamente no
`db load`/`complete` — e pode ser executada isoladamente após qualquer carga.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db search
```

**Build-and-swap (zero downtime de leitura):**
1. `CREATE UNLOGGED TABLE busca_estabelecimento_new AS SELECT ...` (CTAS rápido, sem WAL);
2. validação de contagem (1 linha por linha de `estabelecimento` — o build aborta se divergir, mantendo a tabela vigente);
3. `ALTER TABLE ... SET LOGGED` (durabilidade antes de indexar);
4. PK em `cnpj_completo` + índices com sufixo `_new` (2 GIN trigram nas colunas de nome normalizadas + 2 btrees compostos) + `ANALYZE`;
5. **uma única transação**: `DROP` da tabela vigente + `RENAME` da nova + renomeação de PK/índices — leitores nunca veem estado intermediário.

O comando é idempotente: restos de builds interrompidos (`*_new`) são
descartados no início, e a troca funciona tanto na primeira execução
(sem tabela vigente) quanto nas recriações mensais.

**Requisitos:** extensão `unaccent` (criada no `db init`) e tabelas
`estabelecimento`/`empresa` carregadas. Tamanho estimado em produção:
10–12 GB para 72M de linhas.

---

## Comandos `db views create` e `db views refresh`

Comandos **opcionais** para criação e atualização de Materialized Views (MVs).

As MVs pré-computam estatísticas agregadas que reduzem consultas de minutos para milissegundos.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--concurrent` | flag | - | Usa `REFRESH CONCURRENTLY` (apenas para `refresh`) |

```bash
# Criar/recriar todas as Materialized Views
python etl.py db views create

# Atualizar dados das MVs (após nova carga)
python etl.py db views refresh

# Atualizar sem bloquear leituras (requer índice único nas MVs)
python etl.py db views refresh --concurrent
```

### Materialized Views disponíveis

| View | Descrição | Tempo estimado |
|------|-----------|----------------|
| `mv_stats_estado` | Estatísticas agregadas por estado | ~2 min |
| `mv_stats_municipio` | Estatísticas agregadas por município | ~5 min |
| `mv_stats_cnae` | Estatísticas agregadas por CNAE | ~3 min |
| `mv_stats_cnae_estado` | Estatísticas detalhadas CNAE x Estado | ~10 min |
| `mv_abertura_periodo` | Aberturas por mês/estado (desde 2000) | ~8 min |
| `mv_top_cnaes_cidade` | Top 20 CNAEs por cidade | ~15 min |
| `mv_stats_natureza_juridica_estado` | Estatísticas por natureza jurídica x estado | ~6 min |
| `mv_stats_natureza_juridica_municipio` | Estatísticas por natureza jurídica x município | ~10 min |
| `mv_stats_natureza_juridica` | Estatísticas agregadas por natureza jurídica | ~3 min |

**Arquivos SQL:** Os scripts estão em `sql/materialized_views/` e são executados na ordem alfabética.

**Periodicidade de refresh recomendada:**
- `mv_stats_estado`, `mv_stats_cnae`: Diário
- Demais MVs: Semanal ou quinzenal

**Espaço estimado:** ~2 GB

---

## Comando `complete`

Executa o pipeline completo: **download + carga + Materialized Views** em sequência.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--month` | `MM/AAAA` | Último mês | Mês de referência |
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--download-dir` | `path` | `data/downloads` | Diretório de download |
| `--workers` | `int` | `10` | Downloads simultâneos |
| `--clean` | flag | - | Remove arquivos antes de baixar |
| `--skip-index` | flag | - | Não cria índices |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection |
| `--parallel` | flag | - | Usa multi-threading |
| `--skip-download` | flag | - | Não baixa os arquivos, executa apenas as etapas do banco |
| `--skip-views` | flag | - | Não cria Materialized Views ao final |

```bash
# Pipeline completo (inclui índices e Materialized Views)
python etl.py complete --month 07/2026 --parallel --clean

# Apenas etapas do banco (arquivos já baixados)
python etl.py complete --month 07/2026 --parallel --skip-download
```

> Este comando leva **horas**. Em servidor, rode em segundo plano em vez de
> deixá-lo preso ao terminal — via Docker com `docker compose run -d --name ...`
> ([Guia Docker](docker.md#execução-em-segundo-plano-detached)) ou, em execução
> local com Python, com `nohup python etl.py complete ... &`. Nos dois casos, o
> acompanhamento é o mesmo: `tail -f data/logs/etl-$(date +%F).log`.

---

## Execução por Etapas

O ETL pode ser executado **etapa por etapa**, útil para:
- Retomar de um ponto específico após falha
- Validar correções sem reprocessar tudo
- Maior controle sobre o processo

| Etapa | Comando | Descrição |
|-------|---------|-----------|
| 1 | `python etl.py db init` | Cria schema e tabelas |
| 2 | `python etl.py download` | Baixa arquivos da RFB |
| 3 | `python etl.py db load --only-data` | Carrega dados (sem extras) |
| 4 | `python etl.py db patch` | Aplica correções estáticas |
| 5 | `python etl.py db logged` | Converte tabelas UNLOGGED para LOGGED |
| 6 | `python etl.py db pk` | Adiciona chaves primárias |
| 7 | `python etl.py db index` | Cria todos os índices (básicos + avançados) |
| 8 | `python etl.py db fk` | Cria chaves estrangeiras |
| 9 | `python etl.py db search` | Constrói a tabela de busca `busca_estabelecimento` |
| 10 | `python etl.py db views create` | *(Opcional)* Cria Materialized Views |

**Retomar após falha:**

```bash
# Exemplo: erro na criação de índices
# Após corrigir, retome:
python etl.py db index
python etl.py db fk
```

**Fluxo completo etapa por etapa:**

```bash
python etl.py db init
python etl.py download --month 07/2026
python etl.py db load --only-data --month 07/2026
python etl.py db patch
python etl.py db logged
python etl.py db pk
python etl.py db index
python etl.py db fk
python etl.py db search
```

---

## Ajuda

```bash
python etl.py --help
python etl.py download --help
python etl.py db --help
python etl.py db load --help
python etl.py db views --help
python etl.py db views create --help
python etl.py db views refresh --help
```
