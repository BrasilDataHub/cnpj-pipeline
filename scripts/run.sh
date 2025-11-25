#!/usr/bin/env bash
# Script unificado para automacao do projeto RFB-CNPJ-ETL
# Compativel com macOS e Linux (Debian/Ubuntu)

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Navega para a raiz do projeto
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
cd "$PROJECT_ROOT"

# Variaveis globais
USE_DOCKER=false
ENGINE_FLAG=""

# Funcao para exibir ajuda
show_help() {
    echo ""
    echo "Uso: ./scripts/run.sh <comando> [opcoes]"
    echo ""
    echo "Comandos disponiveis:"
    echo ""
    echo "  setup       Configura o ambiente do projeto (cria venv e instala dependencias)"
    echo "  complete    Executa o ciclo completo do ETL (download + carga)"
    echo "  download    Executa apenas o download dos dados da Receita Federal"
    echo "  load        Executa apenas a carga dos dados no banco"
    echo "  stop        Para os containers Docker (se estiverem rodando)"
    echo "  help        Exibe esta mensagem de ajuda"
    echo ""
    echo "Opcoes:"
    echo ""
    echo "  --docker    Inicia o PostgreSQL via Docker antes de executar o comando"
    echo "              (automaticamente usa --engine postgres)"
    echo ""
    echo "Exemplos:"
    echo "  ./scripts/run.sh setup             # Configurar o ambiente pela primeira vez"
    echo "  ./scripts/run.sh setup --docker    # Configurar ambiente e iniciar PostgreSQL via Docker"
    echo "  ./scripts/run.sh complete          # Executar pipeline completo (PostgreSQL configurado)"
    echo "  ./scripts/run.sh complete --docker # Executar pipeline completo (PostgreSQL via Docker)"
    echo ""
}

# Funcao para verificar/criar arquivo .env
check_env_file() {
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            echo -e "${YELLOW}Arquivo .env nao encontrado. Criando a partir do .env.example...${NC}"
            cp .env.example .env
            echo -e "${GREEN}Arquivo .env criado com sucesso.${NC}"
            echo -e "${YELLOW}IMPORTANTE: Edite o arquivo .env com suas configuracoes antes de usar o PostgreSQL.${NC}"
            echo ""
        else
            echo -e "${YELLOW}Aviso: Arquivos .env e .env.example nao encontrados.${NC}"
            echo ""
        fi
    fi
}

# Funcao para verificar se Python 3 esta instalado
check_python() {
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}ERRO: Python 3 nao encontrado.${NC}"
        echo ""
        echo "Instale o Python 3 antes de continuar:"
        echo ""
        echo "  macOS (via Homebrew):"
        echo "    brew install python3"
        echo ""
        echo "  Debian/Ubuntu:"
        echo "    sudo apt update && sudo apt install python3 python3-venv python3-pip"
        echo ""
        exit 1
    fi
}

# Funcao para verificar se Docker esta instalado
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}ERRO: Docker nao encontrado.${NC}"
        echo ""
        echo "Instale o Docker antes de usar a flag --docker:"
        echo ""
        echo "  macOS:"
        echo "    https://docs.docker.com/desktop/install/mac-install/"
        echo ""
        echo "  Debian/Ubuntu:"
        echo "    https://docs.docker.com/engine/install/ubuntu/"
        echo ""
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        echo -e "${RED}ERRO: Docker Compose nao encontrado.${NC}"
        exit 1
    fi
}

# Funcao para iniciar o PostgreSQL via Docker
start_docker_postgres() {
    echo -e "${BLUE}Iniciando PostgreSQL via Docker...${NC}"
    echo ""

    # Verifica se docker compose (v2) ou docker-compose (v1) esta disponivel
    if docker compose version &> /dev/null; then
        DOCKER_COMPOSE="docker compose"
    else
        DOCKER_COMPOSE="docker-compose"
    fi

    # Inicia o container
    $DOCKER_COMPOSE up -d

    echo ""
    echo -e "${YELLOW}Aguardando o PostgreSQL inicializar...${NC}"

    # Aguarda o container ficar healthy (maximo 60 segundos)
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if $DOCKER_COMPOSE ps | grep -q "healthy"; then
            echo -e "${GREEN}PostgreSQL iniciado e pronto para conexoes!${NC}"
            echo ""
            return 0
        fi

        # Verifica se o container esta rodando
        if ! $DOCKER_COMPOSE ps | grep -q "Up"; then
            echo -e "${RED}ERRO: Container do PostgreSQL nao esta rodando.${NC}"
            echo "Verifique os logs com: $DOCKER_COMPOSE logs"
            exit 1
        fi

        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done

    # Se chegou aqui, timeout
    echo ""
    echo -e "${YELLOW}Aviso: Timeout aguardando healthcheck, mas o container esta rodando.${NC}"
    echo -e "${YELLOW}Continuando... Se houver erro de conexao, aguarde mais alguns segundos.${NC}"
    echo ""
}

# Funcao para parar containers Docker
stop_docker() {
    echo -e "${BLUE}Parando containers Docker...${NC}"

    if docker compose version &> /dev/null; then
        docker compose down
    else
        docker-compose down
    fi

    echo -e "${GREEN}Containers parados.${NC}"
}

# Funcao para verificar se o ambiente virtual existe
check_venv() {
    if [ ! -d ".venv" ]; then
        echo -e "${RED}ERRO: Ambiente virtual nao encontrado.${NC}"
        echo "Execute primeiro: ./scripts/run.sh setup"
        exit 1
    fi
}

# Funcao para ativar o ambiente virtual
activate_venv() {
    source .venv/bin/activate
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERRO: Falha ao ativar o ambiente virtual.${NC}"
        exit 1
    fi
}

# Comando: setup
cmd_setup() {
    echo "--- Configuracao de Ambiente para macOS/Linux ---"
    echo ""

    # Verificar/criar .env
    check_env_file

    # Verificar Python
    check_python
    PYTHON_VERSION=$(python3 --version)
    echo -e "${GREEN}Python encontrado: $PYTHON_VERSION${NC}"
    echo ""

    # Se --docker foi passado, verificar e iniciar Docker
    if [ "$USE_DOCKER" = true ]; then
        check_docker
        start_docker_postgres
    fi

    # Criar ambiente virtual
    if [ -d ".venv" ]; then
        echo -e "${YELLOW}Ambiente virtual '.venv' ja existe. Pulando etapa de criacao.${NC}"
    else
        echo "Criando ambiente virtual em '.venv'..."
        python3 -m venv .venv
        if [ $? -ne 0 ]; then
            echo -e "${RED}ERRO: Falha ao criar o ambiente virtual.${NC}"
            exit 1
        fi
    fi
    echo ""

    # Instalar dependencias
    echo "Instalando dependencias a partir de requirements.txt..."
    echo "Isso pode demorar alguns minutos..."
    echo ""

    .venv/bin/pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERRO: Falha ao instalar as dependencias.${NC}"
        exit 1
    fi
    echo ""

    echo "-----------------------------------------------------------------"
    echo -e "${GREEN}SUCESSO! O ambiente foi configurado corretamente.${NC}"
    echo "-----------------------------------------------------------------"
    echo ""
    echo "Agora voce pode executar:"
    echo ""
    if [ "$USE_DOCKER" = true ]; then
        echo "  ./scripts/run.sh complete --docker   # Pipeline completo (PostgreSQL)"
        echo "  ./scripts/run.sh download --docker   # Apenas download"
        echo "  ./scripts/run.sh load --docker       # Apenas carga no PostgreSQL"
        echo "  ./scripts/run.sh stop                # Parar o PostgreSQL"
    else
        echo "  ./scripts/run.sh complete   # Pipeline completo (download + carga)"
        echo "  ./scripts/run.sh download   # Apenas download"
        echo "  ./scripts/run.sh load       # Apenas carga no banco"
    fi
    echo ""
}

# Comando: complete
cmd_complete() {
    check_env_file
    check_venv
    activate_venv

    if [ "$USE_DOCKER" = true ]; then
        check_docker
        start_docker_postgres
        ENGINE_FLAG="--engine postgres"
    fi

    echo ""
    python3 etl.py complete $ENGINE_FLAG
    echo ""
}

# Comando: download
cmd_download() {
    check_env_file
    check_venv
    activate_venv
    echo ""
    python3 etl.py download
    echo ""
}

# Comando: load
cmd_load() {
    check_env_file
    check_venv
    activate_venv

    if [ "$USE_DOCKER" = true ]; then
        check_docker
        start_docker_postgres
        ENGINE_FLAG="--engine postgres"
    fi

    echo ""
    python3 etl.py db load $ENGINE_FLAG
    echo ""
}

# Processa argumentos
COMMAND=""
for arg in "$@"; do
    case "$arg" in
        --docker)
            USE_DOCKER=true
            ;;
        setup|complete|download|load|stop|help|--help|-h)
            if [ -z "$COMMAND" ]; then
                COMMAND="$arg"
            fi
            ;;
        *)
            if [ -z "$COMMAND" ]; then
                COMMAND="$arg"
            fi
            ;;
    esac
done

# Comando padrao
COMMAND="${COMMAND:-help}"

# Executa o comando
case "$COMMAND" in
    setup)
        cmd_setup
        ;;
    complete)
        cmd_complete
        ;;
    download)
        cmd_download
        ;;
    load)
        cmd_load
        ;;
    stop)
        check_docker
        stop_docker
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}ERRO: Comando desconhecido: $COMMAND${NC}"
        show_help
        exit 1
        ;;
esac
