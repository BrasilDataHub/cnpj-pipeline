# Execução com Docker

O projeto inclui suporte completo a Docker, permitindo executar o ETL de forma portátil em qualquer ambiente
(local, servidor, cloud). Os arquivos baixados são persistidos no host através de volumes mapeados.

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

# Pipeline completo (download + carga + índices)
docker compose run --rm etl complete --month 11/2025 --parallel

# Apenas download
docker compose run --rm etl download --month 11/2025 --workers 10

# Apenas carga (arquivos já baixados)
docker compose run --rm etl db load --month 11/2025 --parallel

# Criar Materialized Views (opcional, após carga)
docker compose run --rm etl db views create

# Atualizar Materialized Views
docker compose run --rm etl db views refresh --concurrent

# Listar meses disponíveis
docker compose run --rm etl get-availables

# Ver ajuda
docker compose run --rm etl --help
```

## Volumes Mapeados

| Host | Container | Descrição |
|------|-----------|-----------|
| `./data/downloads` | `/app/data/downloads` | Arquivos ZIP baixados (~6GB) |
| `./data/locations` | `/app/data/locations` | CSVs do IBGE (somente leitura) |
| `./docker/volumes/postgresql` | `/var/lib/postgresql/data` | Dados do PostgreSQL (~40GB) |

> Os downloads são persistidos no host, permitindo reutilização entre execuções.

## Execução em Servidores Remotos

O Docker facilita a execução em servidores com mais recursos (CPU, RAM, SSD):

```bash
# Clone no servidor remoto
git clone https://github.com/brasildatahub/rfb-cnpj-etl.git
cd rfb-cnpj-etl

# Configure e execute
cp .env.example .env
# Edite .env conforme necessário

docker compose up -d postgres
docker compose run --rm etl complete --month 11/2025 --parallel
```

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
  -v ./data/locations:/app/data/locations:ro \
  ghcr.io/brasildatahub/cnpj-etl:latest \
  complete --month 11/2025 --parallel
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
  ghcr.io/brasildatahub/cnpj-etl:latest \
  complete --month 11/2025 --parallel

# Apenas download
docker run --rm \
  -v ./data/downloads:/app/data/downloads \
  ghcr.io/brasildatahub/cnpj-etl:latest \
  download --month 11/2025 --workers 10

# Apenas carga (arquivos já baixados)
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  -v ./data/downloads:/app/data/downloads \
  ghcr.io/brasildatahub/cnpj-etl:latest \
  db load --month 11/2025 --parallel

# Criar Materialized Views (opcional, após carga)
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  ghcr.io/brasildatahub/cnpj-etl:latest \
  db views create

# Atualizar Materialized Views
docker run --rm \
  -e POSTGRES_HOST=seu-host \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=seu-usuario \
  -e POSTGRES_PASSWORD=sua-senha \
  -e POSTGRES_DBNAME=dados_cnpj \
  ghcr.io/brasildatahub/cnpj-etl:latest \
  db views refresh --concurrent

# Listar meses disponíveis
docker run --rm ghcr.io/brasildatahub/cnpj-etl:latest get-availables

# Ver ajuda
docker run --rm ghcr.io/brasildatahub/cnpj-etl:latest --help
```

