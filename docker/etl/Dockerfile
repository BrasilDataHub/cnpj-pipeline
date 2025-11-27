# =============================================================================
# Dockerfile - ETL CNPJ da Receita Federal
# =============================================================================
# Imagem otimizada para execução do pipeline de ETL dos dados públicos de CNPJ.
# Suporta download, transformação e carga em PostgreSQL.
#
# Uso:
#   docker compose run --rm etl complete --month 11/2025 --parallel
#   docker compose run --rm etl download --month 11/2025
#   docker compose run --rm etl db load --month 11/2025 --parallel
# =============================================================================

FROM python:3.13-slim

# Metadados da imagem
LABEL maintainer="BrasilDataHub"
LABEL description="ETL para dados públicos de CNPJ da Receita Federal"
LABEL version="1.0"

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema necessárias para psycopg2-binary
# e outras libs que possam ser necessárias
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copiar e instalar dependências Python primeiro (melhor cache de layers)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código-fonte
COPY etl.py .
COPY src/ ./src/

# Copiar scripts SQL (materialized views, etc.)
COPY sql/ ./sql/

# Copiar dados estáticos (CSVs do IBGE)
COPY data/locations/ ./data/locations/

# Criar diretório para downloads (será sobrescrito pelo volume)
RUN mkdir -p /app/data/downloads

# Usuário não-root para segurança (opcional, mas recomendado)
RUN useradd --create-home --shell /bin/bash etluser \
    && chown -R etluser:etluser /app
USER etluser

# Entrypoint para o CLI do ETL
ENTRYPOINT ["python", "etl.py"]

# Comando padrão (pode ser sobrescrito)
CMD ["--help"]
