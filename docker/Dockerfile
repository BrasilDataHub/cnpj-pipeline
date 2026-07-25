# =============================================================================
# Dockerfile - ETL CNPJ da Receita Federal
# =============================================================================
# Imagem otimizada para execução do pipeline de ETL dos dados públicos de CNPJ.
# Suporta download, transformação e carga em PostgreSQL.
#
# Publicada em ghcr.io/brasildatahub/cnpj-pipeline
#
# Uso:
#   docker compose run --rm etl complete --month 07/2026 --parallel
#   docker run --rm ghcr.io/brasildatahub/cnpj-pipeline:latest --help
# =============================================================================

FROM python:3.13-slim

# Metadados da imagem (a CI acrescenta os labels OCI via docker/metadata-action)
LABEL org.opencontainers.image.title="cnpj-pipeline"
LABEL org.opencontainers.image.description="ETL dos dados públicos de CNPJ da Receita Federal para PostgreSQL"
LABEL org.opencontainers.image.source="https://github.com/BrasilDataHub/cnpj-pipeline"
LABEL org.opencontainers.image.licenses="MIT"

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Diretório de trabalho
WORKDIR /app

# Sem dependências de sistema: psycopg2-binary embute a libpq e é distribuída
# como wheel pré-compilada para amd64 e arm64 — instalar gcc/libpq-dev só
# aumentaria a imagem e o tempo de build multi-arch (emulação QEMU).

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
