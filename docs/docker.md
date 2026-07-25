# Execução com Docker

O projeto inclui suporte completo a Docker, permitindo executar o ETL de forma portátil em qualquer ambiente
(local, servidor, cloud). Os arquivos baixados são persistidos no host através de volumes mapeados.

- [Imagem do PostgreSQL](#imagem-do-postgresql)
- [Configuração](#configuração)
- [Comandos Docker](#comandos-docker)
- [Execução em Segundo Plano (detached)](#execução-em-segundo-plano-detached)
- [Logs](#logs)
- [Ciclo de Vida do Container](#ciclo-de-vida-do-container)
- [Volumes Mapeados](#volumes-mapeados)
- [Permissões de Volumes](#permissões-de-volumes-importante)
- [Execução em Servidores Remotos](#execução-em-servidores-remotos)
- [Execução Direta com Docker Run](#execução-direta-com-docker-run)
- [Diagnóstico](#diagnóstico)

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
tail -f data/logs/etl-$(date -u +%F).log     # recomendado: só mensagens de etapa
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

tail -f data/logs/etl-$(date -u +%F).log
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

## Logs

O pipeline escreve em **dois lugares independentes**:

### 1. Saída do container (sempre)

Mensagens de etapa e barras de progresso do `tqdm`. `docker logs` captura tudo,
mas essa saída **só existe enquanto o container existir**: `docker rm` (ou
`--rm`) a apaga.

### 2. Arquivo de log da aplicação (sempre ativo)

Gravado em `data/logs/etl-AAAA-MM-DD.log`, em modo append e com flush por linha
— pode ser acompanhado em tempo real. Como `data/logs` é volume do host, o
arquivo **sobrevive à remoção do container**, e é a fonte mais confiável para
auditar execuções longas:

```bash
tail -f data/logs/etl-$(date -u +%F).log            # acompanhar ao vivo
grep -E '\[(ERROR|WARNING)' data/logs/etl-*.log     # triagem de problemas
```

O caminho é definido por `--log-file` (prioridade) ou pela env `LOG_FILE`, que
aceita quatro formas:

| Valor | Resultado |
|---|---|
| (vazio) | `data/logs/etl-AAAA-MM-DD.log` (default) |
| `/var/log/etl/etl-{date}.log` | `{date}` substituído por `AAAA-MM-DD` |
| `/var/log/etl/` (com barra final, ou diretório existente) | `/var/log/etl/etl-AAAA-MM-DD.log` |
| `/var/log/etl/pipeline.log` | `/var/log/etl/pipeline-AAAA-MM-DD.log` (a data é inserida antes da extensão) |

Caminhos relativos são resolvidos a partir de `/app` (o `WORKDIR` do container).
Se apontar o log para fora de `/app/data/logs`, lembre-se de montar também esse
outro diretório — caso contrário o arquivo morre com o container.

**Fuso horário:** o container roda em **UTC**, tanto nos horários das mensagens
quanto na data do nome do arquivo (por isso os exemplos usam `date -u +%F`).
Para horário de Brasília, passe `-e TZ=America/Sao_Paulo` (a imagem já traz o
tzdata) — aí o `tail` com a data local (`date +%F`) volta a bater.

## Ciclo de Vida do Container

| Ação | Comando | Observação |
|---|---|---|
| Listar em execução | `docker ps` | Só containers rodando |
| Listar todos | `docker ps -a --filter name=cnpj-run` | Inclui os já terminados (`exited`) |
| Interromper | `docker stop cnpj-run-2026-07` | `SIGTERM` + 10 s até o `SIGKILL`; a carga para onde estiver |
| Interromper na hora | `docker kill cnpj-run-2026-07` | `SIGKILL` imediato — evite: pode deixar tabelas parcialmente carregadas |
| Reiniciar | `docker restart cnpj-run-2026-07` | **Reexecuta o comando desde o início** — não há retomada. Para não rebaixar os ZIPs, prefira um comando novo com `--skip-download` |
| Religar um parado | `docker start -a cnpj-run-2026-07` | Idem: recomeça o mesmo comando do zero |
| Remover | `docker rm cnpj-run-2026-07` | Apaga o container e o `docker logs`; downloads e log em `data/` permanecem |
| Remover à força | `docker rm -f cnpj-run-2026-07` | Para e remove numa tacada |
| Ver o comando executado | `docker inspect -f '{{.Path}} {{.Args}}' cnpj-run-2026-07` | Confere o que rodou |
| Ver o código de saída | `docker inspect -f 'exit={{.State.ExitCode}}' cnpj-run-2026-07` | `0` = sucesso; `1` = erro; `137` = morto por `stop`/OOM |
| Consumo de recursos | `docker stats cnpj-run-2026-07` | CPU e memória em tempo real |
| Abrir um shell na imagem | `docker run --rm -it --entrypoint bash ghcr.io/brasildatahub/cnpj-pipeline:latest` | Inspeção pontual (roda como `etluser`, uid 1000) |

Para o PostgreSQL e demais serviços do compose:

```bash
docker compose ps                    # estado e healthcheck
docker compose logs -f postgres      # log do banco
docker compose stop postgres         # para, preservando os dados
docker compose start postgres        # religa
docker compose restart postgres      # reinicia (ex.: após mudar PG_*)
docker compose down                  # para e REMOVE containers e rede
```

> `docker compose down` **não** apaga `./docker/volumes/postgresql` (bind mount
> no host) — a base sobrevive. Para zerar o banco de verdade, pare o serviço e
> apague esse diretório; a próxima subida refaz o initdb (extensões, role
> `dados_read` e a collation `C`).

Limpeza dos one-shot acumulados:

```bash
docker ps -a --filter name=cnpj-run --filter status=exited   # conferir antes
docker container prune                                        # remove TODOS os parados
```

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
tail -f data/logs/etl-$(date -u +%F).log
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

Todas podem ser passadas com `-e`, `--env-file` ou pelo `environment:` do
compose. O `.env` **não** entra na imagem (é excluído pelo `.dockerignore`).

**Usadas pelo ETL:**

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `POSTGRES_HOST` | Host do PostgreSQL | `localhost` (compose: `postgres`) |
| `POSTGRES_PORT` | Porta do PostgreSQL | `5432` |
| `POSTGRES_USER` | Usuário do PostgreSQL | `postgres` |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL | - |
| `POSTGRES_DBNAME` | Nome do banco de dados | `dados_cnpj` |
| `DOWNLOAD_PATH` | Diretório dos ZIPs baixados (dentro do container) | `/app/data/downloads` |
| `IBGE_CSV_DIR` | Diretório dos CSVs do IBGE (embutidos na imagem) | `/app/data/locations` |
| `LOG_FILE` | Arquivo de log — ver [Logs](#logs) | `data/logs/etl-{date}.log` |
| `RFB_WEBDAV_URL` | URL base WebDAV da Receita Federal (altere se o token mudar) | URL pública atual |
| `TZ` | Fuso horário do container (afeta log e nome do arquivo) | `UTC` |

**Usadas só pelo `docker-compose.yaml`:**

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `IMAGE_TAG` | Tag da imagem do ETL | `latest` |
| `FORWARD_DB_PORT` | Porta do host mapeada para o PostgreSQL | `5432` |
| `ETL_UID` / `ETL_GID` | uid/gid aplicados pelo `etl-init-permissions` aos volumes | `1000`/`1000` |
| `DADOS_READ_PASSWORD` | Senha do role de leitura criado no initdb (sem ela o role nasce `NOLOGIN`) | vazio |
| `PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`, `PG_MAX_WAL_SIZE`, `PG_RANDOM_PAGE_COST`, `PG_SHM_SIZE`, `PG_MEMORY_LIMIT` | Tuning da imagem do PostgreSQL — os defaults já são o cenário atual | ver `infra/postgres/README.md` |

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

## Diagnóstico

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `Permission denied` no log ou nos downloads | Diretórios do host pertencem ao `root` | `sudo chown -R 1000:1000 data/downloads data/logs` ou rode o `etl-init-permissions` |
| Falha na carga de localidades (CSVs do IBGE ausentes) | Volume cobrindo `/app/data` inteiro | Monte só as subpastas (`data/downloads`, `data/logs`) |
| `could not translate host name "postgres"` | `docker run` fora da rede do compose | Use `--network rfb-cnpj-network`, `--add-host postgres:host-gateway` ou aponte `POSTGRES_HOST` para o IP/DNS real |
| `Connection refused` no banco | PostgreSQL ainda subindo ou porta não publicada | `docker compose ps` (healthcheck) e `docker compose logs postgres` |
| Container morre com exit `137` | `docker stop` ou **OOM kill** | `docker inspect -f '{{.State.OOMKilled}}' <nome>`; o serviço `etl` tem limite de 4 GB no compose — ajuste `deploy.resources.limits` |
| PostgreSQL reiniciando em loop | Tuning `PG_*` acima da RAM da máquina | `docker compose logs postgres`; reveja os cenários em `infra/postgres/README.md` |
| `docker logs` vazio depois que terminou | Container criado com `--rm` | Use o arquivo em `data/logs/` (persiste) ou rode sem `--rm` |
| Nada nos logs há muito tempo | Etapa longa sem emissão (criação de índices, `SET LOGGED`) | Confira atividade no banco: `docker compose exec postgres psql -U postgres -d dados_cnpj -c "select pid, state, query from pg_stat_activity where state <> 'idle'"` |
| Horário do log 3 h à frente | Container em UTC | `-e TZ=America/Sao_Paulo` |
| Download recomeça do zero | Volume de downloads não montado | Monte `./data/downloads:/app/data/downloads` e use `--skip-download` quando os ZIPs já existirem |

### Comandos de triagem rápida

```bash
docker ps -a --filter name=cnpj-run                              # o que rodou e como terminou
docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' <nome>
docker logs --tail 100 <nome>                                    # últimas linhas
grep -E '\[(ERROR|WARNING)' data/logs/etl-*.log | tail -20       # erros no log da aplicação
docker stats --no-stream                                         # memória/CPU agora
docker compose logs --tail 100 postgres                          # lado do banco
du -sh data/downloads docker/volumes/postgresql                   # espaço em disco
```
