#!/usr/bin/env bash
# Executa apenas a etapa de carga dos dados no banco

set -e

# Navega para a raiz do projeto
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# Verifica se o ambiente virtual existe
if [ ! -d ".venv" ]; then
    echo "ERRO: Ambiente virtual nao encontrado."
    echo "Execute primeiro: ./scripts/000_preparar_ambiente.sh"
    exit 1
fi

# Ativa o ambiente virtual
source .venv/bin/activate
if [ $? -ne 0 ]; then
    echo "ERRO: Falha ao ativar o ambiente virtual."
    exit 1
fi

echo ""
python -m src.rfb_cnpj_etl.main db load
echo ""

