# rfb-cnpj-etl

ETL completo dos dados públicos de CNPJ para PostgreSQL.

Fonte: [Dados Abertos CNPJ - Receita Federal](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)

## Sobre

Este projeto facilita o acesso, extração e estruturação dos dados públicos do CNPJ, disponibilizados
mensalmente pela Receita Federal, permitindo que desenvolvedores, analistas e pesquisadores utilizem essas informações
em bases relacionais para fins analíticos, acadêmicos ou de integração com outros sistemas.

O total de linhas (somando todas as tabelas) já está na casa dos **200 milhões**.

## Funcionalidades

- Download completo da base de dados CNPJ no site da RFB
- Carga completa em banco de dados PostgreSQL
- Criação de índices otimizados (BTREE, GIN, BRIN, HASH)
- Materialized Views para estatísticas agregadas (consultas em milissegundos)
- Execução por etapas independentes (permite retomar de qualquer ponto)
- **Suporte a Docker** para execução portátil em qualquer ambiente

## Quick Start

### Docker (Recomendado)

```bash
git clone https://github.com/brasildatahub/rfb-cnpj-etl.git
cd rfb-cnpj-etl
cp .env.example .env
# Edite .env com suas configurações

docker compose up -d postgres
docker compose run --rm etl complete --month 01/2026 --parallel
```

> Requer [Docker](https://docs.docker.com/get-docker/) e [Docker Compose](https://docs.docker.com/compose/install/)

### Instalação Local

```bash
git clone https://github.com/brasildatahub/rfb-cnpj-etl.git
cd rfb-cnpj-etl
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Requer Python 3.9+ e PostgreSQL configurado

## Uso Básico

```bash
# Pipeline completo (download + carga + índices + views)
python etl.py complete --month 01/2026 --parallel --log-file data/logs/etl-{date}.log

# Pipeline sem download (arquivos já baixados)
python etl.py complete --month 01/2026 --parallel --skip-download

# Apenas download
python etl.py download --month 01/2026

# Apenas carga
python etl.py db load --month 01/2026 --parallel

# Criar Materialized Views manualmente (caso use --skip-views)
python etl.py db views create

# Atualizar Materialized Views
python etl.py db views refresh --concurrent

# Converter tabelas UNLOGGED para LOGGED manualmente (já roda no pipeline)
python etl.py db logged

# Reconstruir a tabela de busca enxuta manualmente (já roda no pipeline)
python etl.py db search

# Listar meses disponíveis
python etl.py get-availables

# Ajuda
python etl.py --help
```

## Logs

O CLI grava logs em arquivo para auditoria. Por padrão, o log é salvo em
`data/logs/etl-YYYY-MM-DD.log` (rotação diária simples).

Você pode sobrescrever o caminho via `--log-file` ou pela variável de ambiente `LOG_FILE`:

```bash
# CLI (tem prioridade sobre LOG_FILE)
python etl.py complete --log-file /var/log/etl/etl-{date}.log

# Variável de ambiente (arquivo ou diretório)
export LOG_FILE=data/logs/
```

## Requisitos de Espaço

Cerca de **50GB**:
- ~6GB para downloads
- ~40GB do banco de dados (com índices)

> Os arquivos `.zip` são lidos diretamente, sem extração no disco.

**Recomenda-se ter ao menos 70 GB livres** para garantir estabilidade durante a execução.

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
| [Guia Docker](docs/docker.md) | Configuração, volumes, variáveis de ambiente, execução remota |
| [Referência de Comandos](docs/commands.md) | Todos os comandos, flags e execução por etapas |
| [Configuração](docs/configuration.md) | Personalização, variáveis de ambiente, scripts SQL |
| [Guia do Banco de Dados](docs/database.md) | Estrutura do banco, índices, MVs e consultas |

## Estrutura do Projeto

```
rfb-cnpj-etl/
├── src/rfb_cnpj_etl/      # Código-fonte do ETL
│   ├── main.py            # CLI principal
│   ├── orchestrator.py    # Orquestrador de etapas
│   ├── config.py          # Configurações
│   ├── cnpj_data/         # Download e scraping
│   ├── db/                # Schema e loaders
│   └── utils/             # Utilitários
├── docs/                  # Documentação detalhada
├── sql/                   # Scripts SQL auxiliares
│   └── materialized_views/ # Scripts de MVs
├── data/                  # Downloads e dados IBGE
├── docker/                # Dockerfile e volumes
├── docker-compose.yaml
├── etl.py                 # Wrapper CLI
└── requirements.txt
```

## Contribuição

Contribuições são bem-vindas. Para reportar bugs ou sugerir ideias, abra uma [Issue](https://github.com/brasildatahub/rfb-cnpj-etl/issues).
Para enviar melhorias, crie um [Pull Request](https://github.com/brasildatahub/rfb-cnpj-etl/pulls).

## Licença

Este projeto está licenciado sob os termos da licença MIT. Veja [LICENSE](LICENSE) para mais informações.
