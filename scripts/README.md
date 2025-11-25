# Scripts de Automação

Este diretório contém o script `run.sh` para automatizar tarefas comuns do projeto, como a instalação de dependências e a execução do processo de ETL.

Compatível com **macOS** e **Linux (Debian/Ubuntu)**.

---

## Instalação

### Baixe o Projeto

Clone o repositório ou baixe o `.zip` da página de Releases:

```bash
git clone https://github.com/brasildatahub/rfb-cnpj-etl.git
cd rfb-cnpj-etl
```

### Dê Permissão de Execução ao Script

Antes de executar, torne o script executável:

```bash
chmod +x scripts/run.sh
```

### Configure o Ambiente

Execute o setup padrão (PostgreSQL local ou já configurado):

```bash
./scripts/run.sh setup
```

Para subir um PostgreSQL via Docker junto com o setup:

```bash
./scripts/run.sh setup --docker
```

Aguarde a mensagem **"SUCESSO!"**.

---

## Configuração do Ambiente (.env)

O script verifica automaticamente se o arquivo `.env` existe. Se não existir, ele cria uma cópia a partir do `env.example`.

Para usar o PostgreSQL, edite o arquivo `.env` com suas configurações:

```bash
# Configuracoes do PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=sua_senha_aqui
POSTGRES_DBNAME=dados_cnpj
```

---

## Pré-requisitos

### Python

**macOS (via Homebrew):**

```bash
brew install python3
```

**Debian/Ubuntu:**

```bash
sudo apt update && sudo apt install python3 python3-venv python3-pip
```

### Docker (opcional, para usar PostgreSQL)

Instale o Docker Desktop ou Docker Engine:

- **macOS:** https://docs.docker.com/desktop/install/mac-install/
- **Debian/Ubuntu:** https://docs.docker.com/engine/install/ubuntu/

---

## Como Usar

Execute o script a partir da raiz do projeto passando o comando desejado:

```bash
./scripts/run.sh <comando> [opcoes]
```

### Comandos Disponíveis

| Comando    | Descrição                                                        |
|------------|------------------------------------------------------------------|
| `setup`    | Configura o ambiente (cria `.venv` e instala dependências)       |
| `complete` | Executa o ciclo completo do ETL (download + carga)               |
| `download` | Executa apenas o download dos dados da Receita Federal           |
| `load`     | Executa apenas a carga dos dados no banco                        |
| `stop`     | Para os containers Docker (PostgreSQL)                           |
| `help`     | Exibe a mensagem de ajuda com todos os comandos                  |

### Opções

| Opção      | Descrição                                                        |
|------------|------------------------------------------------------------------|
| `--docker` | Inicia o PostgreSQL via Docker e usa `--engine postgres`         |

---

## Exemplos de Uso

### Fluxo padrão (PostgreSQL já configurado)

```bash
# Configurar o ambiente
./scripts/run.sh setup

# Executar pipeline completo
./scripts/run.sh complete

# Ou executar etapas separadamente
./scripts/run.sh download
./scripts/run.sh load
```

### Fluxo com PostgreSQL via Docker

```bash
# Configurar o ambiente e iniciar PostgreSQL
./scripts/run.sh setup --docker

# Executar pipeline completo com PostgreSQL
./scripts/run.sh complete --docker

# Ou executar etapas separadamente
./scripts/run.sh download
./scripts/run.sh load --docker

# Parar o PostgreSQL quando terminar
./scripts/run.sh stop
```

---

## Notas

- Script testado em **macOS** e **Debian/Ubuntu**.
- Certifique-se de ter pelo menos **50GB de espaço livre** para o processo completo de ETL.
- O arquivo `.env` é criado automaticamente a partir do `.env.example` na primeira execução.
- A flag `--docker` automaticamente adiciona `--engine postgres` aos comandos.
