# Execução com Docker

O projeto inclui suporte completo a Docker, permitindo executar o ETL de forma portátil em qualquer ambiente
(local, servidor, cloud). Os arquivos baixados são persistidos no host através de volumes mapeados.

## Imagem do PostgreSQL

O compose usa a imagem da nossa infra, **`ghcr.io/brasildatahub/postgres:17`**
(repositório `infra/`, publicada pela CI no GHCR com pull anônimo) — não a
`postgres:17` crua. Ela embute:

- `postgresql.conf` gerado no start a partir das envs `PG_*` — os **defaults
  já são o cenário atual** (host compartilhado, ~4 GB para o Postgres), então
  sem env nenhuma o comportamento é o tuning padrão da org, nunca o default
  de fábrica do Postgres (`shared_buffers=128MB` etc.);
- initdb (primeira inicialização, volume vazio) com as extensões `pg_trgm`,
  `unaccent`, `pg_stat_statements` e `btree_gin` e o role de leitura
  `dados_read` com timeouts de servidor;
- `pg_stat_statements` pré-carregado (`shared_preload_libraries`).

Para retunar em outra máquina, sobrescreva as envs `PG_*` no `.env` — a
tabela completa e os blocos por cenário (compartilhada 8 GB, dedicada 64 GB,
dedicada 128 GB) estão em `infra/postgres/README.md`.

## Configuração

```bash
# Copie o arquivo de exemplo e configure
cp .env.example .env
```

Edite o arquivo `.env` com suas configurações:

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=sua_senha_segura
POSTGRES_DBNAME=dados_cnpj
```

## Comandos Docker

```bash
# Subir apenas o PostgreSQL
docker compose up -d postgres

# Preparar permissões dos volumes (recomendado na primeira vez)
ETL_UID=$(id -u) ETL_GID=$(id -g) docker compose up --abort-on-container-exit etl-init-permissions

# Pipeline completo (download + carga + índices)
docker compose run --rm etl complete --month 07/2026 --parallel

# Apenas download
docker compose run --rm etl download --month 07/2026 --workers 10

# Apenas carga (arquivos já baixados)
docker compose run --rm etl db load --month 07/2026 --parallel

# Criar Materialized Views (opcional, após carga)
docker compose run --rm etl db views create

# Atualizar Materialized Views
docker compose run --rm etl db views refresh --concurrent

# Listar meses disponíveis
docker compose run --rm etl get-availables

# Ver ajuda
docker compose run --rm etl --help
```

## Execução em Segundo Plano (detached)

Os comandos das seções acima rodam em **primeiro plano**: a saída fica presa ao
terminal e, numa sessão SSH, fechar o terminal deixa a execução sem
acompanhamento. Como o `complete` de um mês leva **horas**, em servidor a forma
correta é rodar em segundo plano com `-d` e acompanhar pelos logs.

### Com Docker Compose

```bash
# Dispara em segundo plano; imprime o ID do container e devolve o terminal
docker compose run -d --name cnpj-run-2026-07 etl complete --month 07/2026 --parallel

# Acompanhar (Ctrl+C encerra só o acompanhamento, não o pipeline)
tail -f data/logs/etl-$(date +%F).log     # recomendado: só mensagens de etapa
docker logs -f cnpj-run-2026-07           # stdout bruto do container

# Situação atual e código de saída ao terminar
docker ps --filter name=cnpj-run-2026-07
docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' cnpj-run-2026-07

# Interromper antes do fim, se necessário
docker stop cnpj-run-2026-07

# Remover o container depois de conferir o resultado
docker rm cnpj-run-2026-07
```

### Com `docker run`

```bash
docker run -d --name cnpj-run-2026-07 --env-file .env \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 07/2026 --parallel

tail -f data/logs/etl-$(date +%F).log
```

### Como acompanhar

| Fonte | Comando | Conteúdo |
|---|---|---|
| Arquivo de log (host) | `tail -f data/logs/etl-AAAA-MM-DD.log` | Mensagens de etapa, com horário e tempo decorrido |
| Log do container | `docker logs -f <nome>` | Mesmas mensagens **mais** as barras de progresso |
| Últimas linhas | `docker logs --tail 50 <nome>` | Fecha o acompanhamento sem seguir o fluxo |
| Estado / saída | `docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' <nome>` | `running`/`exited` e código de saída (`0` = sucesso) |

O arquivo em `data/logs/` é gravado pelo próprio pipeline (sempre ativo, veja
[Configuração](configuration.md#logs-e-auditoria)) e persiste no host mesmo
depois de o container ser removido. As barras de progresso do `tqdm` **não**
vão para esse arquivo — elas se redesenham com `\r` e só fazem sentido no
terminal, o que também deixa a saída do `docker logs` visualmente poluída.

### Cuidados

- **Sempre use `--name`** ao rodar em segundo plano. Sem ele o container recebe
  um nome gerado e você precisa descobrí-lo com `docker ps` para ver os logs.
- **Não combine `-d` com `--rm`.** O container é apagado assim que o processo
  termina, levando junto o `docker logs` e o código de saída — você perde a
  única evidência de *como* a execução terminou. Remova manualmente com
  `docker rm` depois de conferir o resultado.
- **Não use políticas de restart** (`--restart always`, `unless-stopped`) neste
  pipeline: ele é um job de execução única e seria reiniciado do zero a cada
  término, refazendo a carga inteira.
- **Um container por vez.** Duas execuções simultâneas do `complete` escrevem
  nas mesmas tabelas e no mesmo diretório de downloads. Confira com
  `docker ps` antes de disparar outra.
- **Reboot do servidor encerra o job.** Não há retomada automática; reexecute o
  comando (use `--skip-download` se os ZIPs já estiverem em `data/downloads`).

## Volumes Mapeados

| Host | Container | Descrição |
|------|-----------|-----------|
| `./data/downloads` | `/app/data/downloads` | Arquivos ZIP baixados (~6GB) |
| `./data/logs` | `/app/data/logs` | Logs do ETL (rotação diária simples) |
| `./data/locations` | `/app/data/locations` | CSVs do IBGE (somente leitura) |
| `./docker/volumes/postgresql` | `/var/lib/postgresql/data` | Dados do PostgreSQL (~40GB) |

> Os downloads são persistidos no host, permitindo reutilização entre execuções.

> **Nunca monte `/app/data` inteiro** (`-v ./data:/app/data`): os CSVs do IBGE
> vêm dentro da imagem em `/app/data/locations` e seriam encobertos pela pasta
> do host, fazendo a carga de localidades falhar. Monte apenas as subpastas.

## Permissões de Volumes (Importante)

A imagem do ETL roda como usuário não-root. Em ambientes Docker, se os diretórios do host
forem criados como `root`, o container não conseguirá escrever em `/app/data/downloads` e `/app/data/logs`.

Soluções recomendadas:

```bash
# Ajuste rápido no host (substitua 1000 se necessário)
sudo chown -R 1000:1000 ./data/downloads ./data/logs
sudo chmod -R 0775 ./data/downloads ./data/logs
```

Ou rode o init service que já existe no `docker-compose.yaml` (cria pastas e ajusta permissões):

```bash
ETL_UID=$(id -u) ETL_GID=$(id -g) docker compose up --abort-on-container-exit etl-init-permissions
```

## Execução em Servidores Remotos

O Docker facilita a execução em servidores com mais recursos (CPU, RAM, SSD):

```bash
# Clone no servidor remoto
git clone https://github.com/brasildatahub/cnpj-pipeline.git
cd cnpj-pipeline

# Configure e execute
cp .env.example .env
# Edite .env conforme necessário

docker compose up -d postgres
ETL_UID=$(id -u) ETL_GID=$(id -g) docker compose up --abort-on-container-exit etl-init-permissions

# Em segundo plano — não prende a sessão SSH (veja "Execução em Segundo Plano")
docker compose run -d --name cnpj-run-2026-07 etl complete --month 07/2026 --parallel
tail -f data/logs/etl-$(date +%F).log
```

Numa sessão SSH, prefira sempre o modo detached: o `complete` leva horas e uma
queda de conexão no modo interativo deixa você sem acompanhamento da execução.

## Execução Direta com Docker Run

Para executar a imagem Docker diretamente, sem docker-compose, conectando a um PostgreSQL existente em qualquer ambiente:

```bash
docker run --rm \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DBNAME=dados_cnpj \
  -v ./data/downloads:/app/data/downloads \
  -v ./data/logs:/app/data/logs \
  -v ./data/locations:/app/data/locations:ro \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 07/2026 --parallel
```

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `POSTGRES_HOST` | Host do PostgreSQL | `localhost` |
| `POSTGRES_PORT` | Porta do PostgreSQL | `5432` |
| `POSTGRES_USER` | Usuário do PostgreSQL | `postgres` |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL | - |
| `POSTGRES_DBNAME` | Nome do banco de dados | `dados_cnpj` |

### Volumes

| Host | Container | Descrição |
|------|-----------|-----------|
| `./data/downloads` | `/app/data/downloads` | Arquivos ZIP baixados (~6GB) |
| `./data/logs` | `/app/data/logs` | Logs do ETL (rotação diária simples) |
| `./data/locations` | `/app/data/locations` | CSVs do IBGE (somente leitura) |

> O volume `data/locations` é opcional se a imagem já contém os CSVs do IBGE embutidos.

### Exemplos de Comandos

```bash
# Pipeline completo (download + carga + índices)
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  -v ./data/downloads:/app/data/downloads \
  -v ./data/logs:/app/data/logs \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 07/2026 --parallel

# Apenas download
docker run --rm \
  -v ./data/downloads:/app/data/downloads \
  -v ./data/logs:/app/data/logs \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  download --month 07/2026 --workers 10

# Apenas carga (arquivos já baixados)
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  -v ./data/downloads:/app/data/downloads \
  -v ./data/logs:/app/data/logs \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db load --month 07/2026 --parallel

# Criar Materialized Views (opcional, após carga)
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db views create

# Atualizar Materialized Views
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db views refresh --concurrent

# Listar meses disponíveis
docker run --rm ghcr.io/brasildatahub/cnpj-pipeline:latest get-availables

# Ver ajuda
docker run --rm ghcr.io/brasildatahub/cnpj-pipeline:latest --help
```
