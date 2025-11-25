#!/usr/bin/env bash
# Script de Configuracao de Ambiente para macOS e Linux (Debian/Ubuntu)

set -e

echo "--- Script de Configuracao de Ambiente para macOS/Linux ---"
echo ""

# Navega para a raiz do projeto
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# --- Passo 1: Verificar se Python 3 esta instalado ---
echo "Verificando instalacao do Python 3..."
if ! command -v python3 &> /dev/null; then
    echo "ERRO: Python 3 nao encontrado."
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

PYTHON_VERSION=$(python3 --version)
echo "Python encontrado: $PYTHON_VERSION"
echo ""

# --- Passo 2: Criacao do Ambiente Virtual ---
if [ -d ".venv" ]; then
    echo "Ambiente virtual '.venv' ja existe. Pulando etapa de criacao."
else
    echo "Criando ambiente virtual em '.venv'..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "ERRO: Falha ao criar o ambiente virtual."
        exit 1
    fi
fi
echo ""

# --- Passo 3: Instalacao das Dependencias ---
echo "Instalando dependencias a partir de requirements.txt..."
echo "Isso pode demorar alguns minutos..."
echo ""

.venv/bin/pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "ERRO: Falha ao instalar as dependencias."
    exit 1
fi
echo ""

echo "-----------------------------------------------------------------"
echo "SUCESSO! O ambiente foi configurado corretamente."
echo "-----------------------------------------------------------------"
echo ""
echo "Agora voce pode executar os scripts:"
echo ""
echo "- './scripts/run_complete.sh' para executar o download e em seguida a carga de dados"
echo ""
echo "ou, separadamente:"
echo ""
echo "- './scripts/run_download.sh' para realizar apenas o download dos arquivos mais recentes"
echo ""
echo "- './scripts/run_load.sh' para apenas criar e carregar o banco de dados com os arquivos baixados"
echo ""

