# cnpj-pipeline

ETL completo dos dados públicos de CNPJ para PostgreSQL.

```
ghcr.io/brasildatahub/cnpj-pipeline
```

Fonte: [Dados Abertos CNPJ - Receita Federal](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)

## Finalidade

Este projeto facilita o acesso, extração e estruturação dos dados públicos do CNPJ, disponibilizados
mensalmente pela Receita Federal, permitindo que desenvolvedores, analistas e pesquisadores utilizem essas informações
em bases relacionais para fins analíticos, acadêmicos ou de integração com outros sistemas.

O total de linhas (somando todas as tabelas) já está na casa dos **200 milhões**.

- Download completo da base de dados CNPJ no site da RFB
- Carga completa em banco de dados PostgreSQL
- Criação de índices otimizados (BTREE, GIN, BRIN, HASH)
- Materialized Views para estatísticas agregadas (consultas em milissegundos)
- Tabela de busca enxuta (`busca_estabelecimento`) reconstruída com build-and-swap
- Execução por etapas independentes (permite retomar de qualquer ponto)

## Pré-requisitos

| Caminho | Requisitos |
|---|---|
| Python | Python 3.9+ (a imagem oficial usa `python:3.13-slim`), PostgreSQL 17 acessível |
| Docker | [Docker](https://docs.docker.com/get-docker/) e [Docker Compose](https://docs.docker.com/compose/install/) |

**Espaço em disco:** cerca de **50 GB** (~6 GB de downloads + ~40 GB de banco com índices).
Recomenda-se ter ao menos **70 GB livres** para garantir estabilidade durante a execução.
Os arquivos `.zip` são lidos diretamente, sem extração no disco.

## Configuração

```bash
cp .env.example .env
```

Variáveis lidas pelo pipeline (todas opcionais; os valores abaixo são os defaults do código):

| Variável | Descrição | Padrão |
|---|---|---|
| `POSTGRES_HOST` | Host do PostgreSQL | `localhost` |
| `POSTGRES_PORT` | Porta do PostgreSQL | `5432` |
| `POSTGRES_USER` | Usuário do PostgreSQL | `postgres` |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL | — |
| `POSTGRES_DBNAME` | Nome do banco de dados | `dados_cnpj` |
| `DOWNLOAD_PATH` | Diretório dos arquivos ZIP baixados | `data/downloads` |
| `IBGE_CSV_DIR` | Diretório dos CSVs do IBGE | `data/locations` |
| `RFB_WEBDAV_URL` | Endpoint WebDAV da RFB | URL oficial embutida |
| `LOG_FILE` | Caminho do arquivo de log (aceita `{date}`) | `data/logs/etl-YYYY-MM-DD.log` |

Variáveis consumidas apenas pelo `docker-compose.yaml` (não pelo código Python):
`FORWARD_DB_PORT`, `DADOS_READ_PASSWORD`, `PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE`,
`PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`, `PG_MAX_WAL_SIZE`, `PG_RANDOM_PAGE_COST`,
`PG_SHM_SIZE`, `PG_MEMORY_LIMIT`, `ETL_UID`, `ETL_GID`, `IMAGE_TAG`.

> Dentro do container o arquivo `.env` **não** existe (é excluído pelo `.dockerignore`):
> a configuração precisa chegar por `-e`, `--env-file` ou `environment:`.

## Execução com Python

```bash
git clone https://github.com/BrasilDataHub/cnpj-pipeline.git
cd cnpj-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python etl.py --help
```

Exemplos:

```bash
# Pipeline completo (download + carga + índices + views)
python etl.py complete --month 01/2026 --parallel --log-file data/logs/etl-{date}.log

# Pipeline sem download (arquivos já baixados)
python etl.py complete --month 01/2026 --parallel --skip-download

# Etapas isoladas
python etl.py download --month 01/2026
python etl.py db load --month 01/2026 --parallel
python etl.py db views refresh --concurrent
```

## Execução com Docker

A imagem é pública no GHCR — o `pull` não exige autenticação:

```bash
docker pull ghcr.io/brasildatahub/cnpj-pipeline:latest
```

O `ENTRYPOINT` da imagem já é `python etl.py`, então os argumentos do
`docker run` são diretamente os subcomandos do CLI:

```bash
# Pipeline completo contra um PostgreSQL existente
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 01/2026 --parallel

# Apenas download
docker run --rm \
  -v "$PWD/data/downloads:/app/data/downloads" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  download --month 01/2026 --workers 10

# Usando um arquivo .env
docker run --rm --env-file .env \
  -v "$PWD/data:/app/data" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db views refresh --concurrent

# Listar meses disponíveis / ver ajuda
docker run --rm ghcr.io/brasildatahub/cnpj-pipeline:latest get-availables
docker run --rm ghcr.io/brasildatahub/cnpj-pipeline:latest --help
```

**Tags disponíveis:** `latest`, `sha-<commit>`, e versões semânticas (`1.2.3`, `1.2`, `1`)
quando há release. Para fixar uma versão em produção, prefira `sha-<commit>` ou a tag semver.

**Permissões dos volumes:** a imagem roda como usuário não-root (`etluser`, uid 1000).
Se os diretórios do host pertencerem ao `root`, o container não conseguirá escrever:

```bash
sudo chown -R 1000:1000 ./data/downloads ./data/logs
```

## Execução com Docker Compose

O `docker-compose.yaml` sobe o PostgreSQL da infra da organização
(`ghcr.io/brasildatahub/postgres:17`) junto com o pipeline:

```bash
cp .env.example .env

# Baixa as imagens publicadas (sem build local)
docker compose pull

docker compose up -d postgres

# Ajusta permissões dos volumes (recomendado na primeira vez)
ETL_UID=$(id -u) ETL_GID=$(id -g) docker compose up --abort-on-container-exit etl-init-permissions

docker compose run --rm etl complete --month 01/2026 --parallel
```

Outros exemplos:

```bash
docker compose run --rm etl download --month 01/2026 --workers 10
docker compose run --rm etl db load --month 01/2026 --parallel
docker compose run --rm etl db views create
docker compose run --rm etl db views refresh --concurrent
docker compose run --rm etl --help
```

Para fixar uma versão específica da imagem, defina `IMAGE_TAG` no `.env`
(ex.: `IMAGE_TAG=1.2.3`). O padrão é `latest`.

## Construção da imagem localmente

```bash
# Build simples, para a arquitetura da máquina atual
docker build -t cnpj-pipeline:dev -f docker/Dockerfile .

# Build multi-arquitetura, igual ao da CI (requer buildx)
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile -t cnpj-pipeline:dev .

# Via compose
docker compose build etl
```

> O contexto de build é a **raiz do repositório** — o Dockerfile copia
> `etl.py`, `src/`, `sql/` e `data/locations/`.

## Referência de comandos

Opção global disponível em todos os comandos: `--log-file <caminho>` (aceita o
placeholder `{date}`; tem prioridade sobre a variável `LOG_FILE`).

| Comando | Argumentos | O que faz |
|---|---|---|
| `get-availables` | — | Lista os meses disponíveis na RFB |
| `get-latest` | — | Mostra o mês mais recente publicado |
| `get-urls` | `--month MM/AAAA` | Lista as URLs dos arquivos do mês |
| `download` | `--month`, `--clean`, `--workers`, `--download-dir` | Baixa os ZIPs do mês |
| `db init` | `--db-name` | Cria o banco e o schema |
| `db load` | `--month`, `--db-name`, `--download-dir`, `--skip-index`, `--skip-validation`, `--low-memory`, `--parallel true\|false`, `--only-data` | Carga dos dados |
| `db index` | `--db-name` | Cria os índices otimizados |
| `db patch` | `--db-name` | Aplica correções pós-carga |
| `db logged` | `--db-name` | Converte tabelas UNLOGGED → LOGGED |
| `db pk` | `--db-name` | Cria as primary keys |
| `db fk` | `--db-name` | Cria as foreign keys |
| `db search` | `--db-name` | Reconstrói `busca_estabelecimento` (build-and-swap) |
| `db views create` | `--db-name` | Cria as Materialized Views |
| `db views refresh` | `--db-name`, `--concurrent` | Atualiza as Materialized Views |
| `complete` | `--month`, `--db-name`, `--download-dir`, `--workers`, `--clean`, `--parallel`, `--skip-download`, `--skip-index`, `--skip-validation`, `--skip-views`, `--low-memory` | Pipeline completo |

O mesmo comando vale nos três formatos:

```bash
python etl.py db views refresh --concurrent                                    # Python
docker compose run --rm etl db views refresh --concurrent                      # Compose
docker run --rm --env-file .env \
  ghcr.io/brasildatahub/cnpj-pipeline:latest db views refresh --concurrent     # docker run
```

## Publicação da imagem no GHCR

A imagem é construída e publicada pelo workflow
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml),
para `linux/amd64` e `linux/arm64`.

| Gatilho | Tags publicadas |
|---|---|
| Push na `main` | `latest`, `sha-<commit curto>` |
| Push de tag `v1.2.3` | `1.2.3`, `1.2`, `1`, `sha-<commit curto>` |
| `workflow_dispatch` (manual) | conforme a ref selecionada |

Para atualizar a imagem, basta enviar o commit para a `main`:

```bash
git push origin main
gh run watch          # acompanha a execução
```

Para publicar uma versão fixa:

```bash
git tag v1.2.3
git push origin v1.2.3
```

Para republicar sem alterar código, use a aba **Actions → Publica imagem no GHCR
→ Run workflow** (ou `gh workflow run docker-publish.yml`).

Verificando o resultado:

```bash
docker buildx imagetools inspect ghcr.io/brasildatahub/cnpj-pipeline:latest
```

## Logs

O CLI grava logs em arquivo para auditoria. Por padrão, em
`data/logs/etl-YYYY-MM-DD.log` (rotação diária simples). O caminho pode ser
sobrescrito por `--log-file` (prioridade) ou pela variável `LOG_FILE`:

```bash
python etl.py complete --log-file /var/log/etl/etl-{date}.log
export LOG_FILE=data/logs/
```

## Durabilidade (UNLOGGED → LOGGED)

A carga cria as tabelas como `UNLOGGED` para acelerar o `COPY` (sem WAL).
Ao final da carga — logo após os patches e **antes** de PKs/índices — o
pipeline converte todas as tabelas para `LOGGED` (`ALTER TABLE ... SET LOGGED`).
Sem essa conversão, um crash do PostgreSQL **trunca** as tabelas UNLOGGED no
recovery (perda total dos dados); LOGGED também é pré-condição para backup
físico/PITR e réplicas.

Custo da conversão: reescrita completa com WAL, tabela a tabela — estimativa
de **+1–3 h** na janela mensal para as 5 tabelas grandes (`estabelecimento`,
`empresa`, `socio`, `simples`, `estabelecimento_cnae_sec`). Um `max_wal_size`
alto (≥4 GB) reduz checkpoints durante a conversão. A etapa é idempotente
(só converte o que ainda é UNLOGGED) e pode ser executada isoladamente com
`python etl.py db logged`.

## Documentação

| Documento | Descrição |
|-----------|-----------|
| [Guia Docker](docs/docker.md) | Volumes, permissões, execução remota |
| [Referência de Comandos](docs/commands.md) | Todos os comandos, flags e execução por etapas |
| [Configuração](docs/configuration.md) | Personalização, variáveis de ambiente, scripts SQL |
| [Guia do Banco de Dados](docs/database.md) | Estrutura do banco, índices, MVs e consultas |

## Estrutura do Projeto

```
cnpj-pipeline/
├── .github/workflows/
│   └── docker-publish.yml # CI: build multi-arch + push no GHCR
├── src/rfb_cnpj_etl/      # Código-fonte do ETL
│   ├── main.py            # CLI principal (argparse)
│   ├── orchestrator.py    # Orquestrador de etapas
│   ├── config.py          # Configurações
│   ├── cnpj_data/         # Download e scraping
│   ├── db/                # Schema e loaders
│   └── utils/             # Utilitários
├── docs/                  # Documentação detalhada
├── sql/                   # Scripts SQL auxiliares
│   └── materialized_views/ # Scripts de MVs
├── data/                  # Downloads, logs e dados IBGE
├── docker/
│   └── Dockerfile         # Imagem do pipeline
├── docker-compose.yaml
├── etl.py                 # Wrapper CLI
└── requirements.txt
```

## Contribuição

Contribuições são bem-vindas. Para reportar bugs ou sugerir ideias, abra uma [Issue](https://github.com/BrasilDataHub/cnpj-pipeline/issues).
Para enviar melhorias, crie um [Pull Request](https://github.com/BrasilDataHub/cnpj-pipeline/pulls).

## Licença

Este projeto está licenciado sob os termos da licença MIT. Veja [LICENSE](LICENSE) para mais informações.
