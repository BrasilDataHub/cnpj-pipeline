# rfb-cnpj-etl

ETL completo dos dados públicos de CNPJ para PostgreSQL.

Fonte: [Dados Abertos CNPJ - Receita Federal](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)

## Sobre

Este projeto pretende facilitar o acesso, extração e estruturação dos dados públicos do CNPJ, disponibilizados
mensalmente pela Receita Federal, permitindo que desenvolvedores, analistas e pesquisadores utilizem essas informações
em bases relacionais para fins analíticos, acadêmicos ou de integração com outros sistemas.
O total de linhas (somando todas as tabelas) já está na casa dos 200 milhões.

## Funcionalidades

- Download completo da base de dados CNPJ no site da RFB
- Preparação e carga completa em banco de dados PostgreSQL
- Criação de índices otimizados para melhorar o desempenho das consultas
- Execução por etapas independentes (permite retomar de qualquer ponto)

## Instalação

Clone o projeto, crie o ambiente virtual e instale os requisitos com:

```bash
git clone https://github.com/brasildatahub/rfb-cnpj-etl.git
cd rfb-cnpj-etl
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Requer Python 3.9 ou superior

> Para o `PostgreSQL`, é necessário ter o servidor instalado e configurado e definir as credenciais em variáveis
> de ambiente ou em `config.py`.

### Espaço necessário

Cerca de 50GB:

- ~6GB para downloads
- ~40GB do banco de dados (já com índices)

> Os arquivos `.zip` são lidos diretamente, sem extração no disco, o que reduz o uso de espaço temporário.

**Recomenda-se ter ao menos 70 GB livres** para garantir estabilidade durante a execução, especialmente em máquinas
com armazenamento mecânico (HDD).

---

## Referência de Comandos

### Visão Geral

```bash
python etl.py <comando> [opções]
```

| Comando | Descrição |
|---------|-----------|
| `get-availables` | Lista meses disponíveis no site da RFB |
| `get-latest` | Retorna o mês mais recente disponível |
| `get-urls` | Exibe URLs de download para um mês |
| `download` | Baixa arquivos ZIP da RFB |
| `db init` | Cria schema e tabelas no banco |
| `db load` | Carrega dados dos arquivos ZIP |
| `db patch` | Aplica correções estáticas na base |
| `db pk` | Adiciona chaves primárias |
| `db index` | Cria índices |
| `db fk` | Cria chaves estrangeiras |
| `complete` | Executa todo o pipeline (download + carga) |

---

### Comandos de Consulta

```bash
# Lista todos os meses disponíveis
python etl.py get-availables

# Retorna o mês mais recente
python etl.py get-latest

# Exibe URLs de download para um mês
python etl.py get-urls --month 11/2025
```

---

### Comando `download`

Baixa os arquivos ZIP de dados abertos do CNPJ diretamente do site da Receita Federal.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--month` | `MM/AAAA` | Último mês | Mês para baixar |
| `--download-dir` | `path` | `data/downloads` | Diretório de destino |
| `--workers` | `int` | `10` | Downloads simultâneos |
| `--clean` | flag | - | Remove arquivos existentes antes de baixar |

```bash
# Baixar mês mais recente
python etl.py download

# Baixar mês específico
python etl.py download --month 11/2025

# Baixar com limpeza prévia e 4 workers
python etl.py download --month 11/2025 --clean --workers 4
```

---

### Comando `db init`

Cria o schema e as tabelas no banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db init
```

---

### Comando `db load`

Carrega os dados dos arquivos ZIP para o banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--month` | `MM/AAAA` | Último mês | Mês a ser carregado |
| `--download-dir` | `path` | `data/downloads/YYYY-MM` | Pasta com os arquivos ZIP |
| `--skip-index` | flag | - | Não cria índices ao final |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection frequente |
| `--parallel` | flag | - | Usa multi-threading na carga |
| `--only-data` | flag | - | Carrega apenas dados (sem patch/pk/index/fk) |

```bash
# Carga completa padrão
python etl.py db load --month 11/2025

# Carga apenas dados (sem extras)
python etl.py db load --month 11/2025 --only-data

# Carga com paralelismo
python etl.py db load --month 11/2025 --parallel
```

---

### Comandos `db patch`, `db pk`, `db index`, `db fk`

Executam etapas específicas do processo de carga.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db patch    # Aplica correções estáticas
python etl.py db pk       # Adiciona chaves primárias
python etl.py db index    # Cria índices
python etl.py db fk       # Cria chaves estrangeiras
```

---

### Comando `complete`

Executa o pipeline completo: **download + carga** em sequência.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--month` | `MM/AAAA` | Último mês | Mês de referência |
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--download-dir` | `path` | `data/downloads` | Diretório de download |
| `--workers` | `int` | `10` | Downloads simultâneos |
| `--clean` | flag | - | Remove arquivos antes de baixar |
| `--skip-index` | flag | - | Não cria índices |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection |
| `--parallel` | flag | - | Usa multi-threading |

```bash
python etl.py complete --month 11/2025 --parallel --clean
```

---

### Execução por Etapas

O ETL pode ser executado **etapa por etapa**, útil para:
- Retomar de um ponto específico após falha
- Validar correções sem reprocessar tudo
- Maior controle sobre o processo

| Etapa | Comando | Descrição |
|-------|---------|-----------|
| 1 | `python etl.py db init` | Cria schema e tabelas |
| 2 | `python etl.py download` | Baixa arquivos da RFB |
| 3 | `python etl.py db load --only-data` | Carrega dados (sem extras) |
| 4 | `python etl.py db patch` | Aplica correções estáticas |
| 5 | `python etl.py db pk` | Adiciona chaves primárias |
| 6 | `python etl.py db index` | Cria índices |
| 7 | `python etl.py db fk` | Cria chaves estrangeiras |

**Retomar após falha:**

```bash
# Exemplo: erro na criação de índices
# Após corrigir, retome:
python etl.py db index
python etl.py db fk
```

**Fluxo completo etapa por etapa:**

```bash
python etl.py db init
python etl.py download --month 11/2025
python etl.py db load --only-data --month 11/2025
python etl.py db patch
python etl.py db pk
python etl.py db index
python etl.py db fk
```

---

### Ajuda

```bash
python etl.py --help
python etl.py download --help
python etl.py db --help
python etl.py db load --help
```

---

## Personalização

Todas as **constantes globais** como diretórios, downloads simultâneos, entre outras, podem ser ajustadas em
`src/rfb_cnpj_etl/config.py`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DOWNLOAD_DIR` | Diretório de downloads | `data/downloads` |
| `DOWNLOAD_MAX_CONCURRENTS` | Downloads simultâneos | `10` |
| `POSTGRES` | Credenciais do PostgreSQL | `localhost:5432` |
| `BATCH_SIZE` | Tamanho do lote de inserção | `250000` |

### Chaves primárias, estrangeiras e índices

As definições de chaves primárias, estrangeiras e índices podem ser encontradas em `src/rfb_cnpj_etl/db/schema.py`.
Edite conforme a sua necessidade.

### Scripts SQL Auxiliares (Opcionais)

Na pasta `sql/` estão disponíveis **scripts auxiliares** para otimizações avançadas. Esses scripts **não são executados
automaticamente** pelo ETL e devem ser aplicados manualmente conforme a necessidade do seu ambiente.

> **Importante:** Esses scripts são complementares ao fluxo principal. O ETL já cria índices básicos definidos em
> `schema.py`. Os scripts abaixo oferecem otimizações adicionais para cenários específicos.

#### Quando utilizar

Execute esses scripts **após a conclusão do ETL** (após `db fk` ou `complete`), quando:
- Precisar de buscas textuais otimizadas (LIKE, trigrams)
- Quiser estatísticas pré-calculadas para dashboards
- Necessitar de índices especializados para consultas frequentes

#### Scripts disponíveis

| Arquivo | Propósito | Pré-requisitos |
|---------|-----------|----------------|
| `indexes.sql` | ~50 índices otimizados (BTREE, GIN/pg_trgm, BRIN, HASH) para buscas textuais, filtros por localização, datas e CNAEs | Extensão `pg_trgm` (criada automaticamente pelo script) |
| `materialized_views.sql` | 6 views materializadas com estatísticas agregadas por estado, município, CNAE e período | Dados já carregados no banco |
| `general_improvements.sql` | Extensões PostgreSQL, funções de manutenção, validações e configurações de performance | Permissões de superusuário para algumas operações |

#### Como executar

```bash
# Conectar ao banco e executar (substitua as credenciais)
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/indexes.sql
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/materialized_views.sql
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/general_improvements.sql
```

#### Detalhes de cada script

**`indexes.sql`** - Índices para consultas específicas:
- Busca textual com `LIKE '%termo%'` (GIN + pg_trgm)
- Filtros por localização (IBGE, UF, município)
- Consultas por faixa de datas (BRIN para tabelas grandes)
- Lookups por CNPJ completo (HASH para igualdade)

**`materialized_views.sql`** - Estatísticas pré-calculadas:
- `mv_stats_estado`: empresas ativas por estado
- `mv_stats_municipio`: empresas ativas por município
- `mv_stats_cnae`: distribuição por atividade econômica
- `mv_stats_cnae_estado`: CNAEs por estado
- `mv_abertura_periodo`: aberturas por período
- `mv_top_cnaes_cidade`: principais CNAEs por cidade
- Função `refresh_all_mvs()` para atualização

**`general_improvements.sql`** - Configurações e manutenção:
- Extensões: `pg_trgm`, `pg_prewarm`, `pg_stat_statements`
- Funções: `prewarm_critical_indexes()`, `vacuum_analyze_all()`, `table_statistics()`
- Validações: `validate_cnpj_completo()`, `check_referential_integrity()`

---

## Exemplos de Consultas

Para começar a explorar os dados, consulte os arquivos de exemplo abaixo. Eles contêm exemplos práticos de como utilizar
as tabelas e colunas para extrair informações úteis, como buscar uma empresa por CNPJ, listar seus sócios ou filtrar
estabelecimentos por cidade.

- Exemplos para PostgreSQL: [query_postgres.md](sql/query_postgres.md)

---

## Estrutura do Projeto

```bash
rfb-cnpj-etl/
├── src/
│   └── rfb_cnpj_etl/
│       ├── main.py                    # Script principal com argparse
│       ├── orchestrator.py            # Orquestrador de etapas
│       ├── config.py                  # Configurações gerais e constantes
│       ├── cnpj_data/                 # Lógica para download e scraping da base de dados CNPJ
│       │   ├── __init__.py             
│       │   ├── cnpj_public_data.py    # Captura os dados da RFB
│       │   └── cnpj_downloader.py     # Gerencia o download dos arquivos
│       ├── db/                        # Módulos para schema, carga e controle de banco
│       │   ├── __init__.py             
│       │   ├── postgres_builder.py    # Criação do banco de dados (PostgreSQL)
│       │   ├── postgres_loader.py     # Carregamento dos dados no banco (PostgreSQL)
│       │   ├── ibge_loader.py         # Carregamento das tabelas IBGE
│       │   └── schema.py              # Esquema do banco de dados (tabelas, chaves e índices)
│       └── utils/                     # Funções utilitárias
│           ├── __init__.py
│           ├── logger.py              # Print personalizado com hora e tempo de execução
│           ├── progress.py            # Barra e log de progresso
│           ├── db_transformers.py     # Transformação de dados para o banco
│           ├── db_batch_producer.py   # Geração de lotes de dados para carga
│           ├── db_patch.py            # Correções estáticas na base de dados
│           ├── ibge_lookup.py         # Lookup de códigos IBGE
│           └── zip_metadata.py        # Validação e metadados dos arquivos ZIP
├── sql/                               # Scripts SQL auxiliares (execução manual)
│   ├── indexes.sql                    # Índices otimizados para buscas textuais e filtros
│   ├── materialized_views.sql         # Views materializadas com estatísticas agregadas
│   ├── general_improvements.sql       # Extensões, funções de manutenção e validações
│   └── query_postgres.md              # Exemplos de consultas SQL para PostgreSQL
├── data/                              # Diretório padrão para downloads
│   ├── downloads/                     # Arquivos ZIP baixados da RFB
│   └── locations/                     # Dados IBGE (regiões, estados, cidades)
├── docker/                            # Configurações Docker
│   └── volumes/                       # Volumes persistentes
├── etl.py                             # Wrapper para execução do CLI
├── docker-compose.yaml                # Configuração do Docker Compose
├── cnpj-metadados.pdf                 # Dicionário de Dados do CNPJ (Receita Federal)
├── AGENTS.md                          # Guidelines do repositório
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt                   # Dependências do projeto
```

---

## Como Contribuir

Contribuições são bem-vindas. Para reportar bugs ou sugerir ideias, por favor, abra
uma [Issue](https://github.com/brasildatahub/rfb-cnpj-etl/issues). Para enviar melhorias no código ou na documentação,
crie um [Pull Request](https://github.com/brasildatahub/rfb-cnpj-etl/pulls).

## Licença

Este projeto está licenciado sob os termos da licença MIT.
Veja o arquivo [LICENSE](LICENSE) para mais informações.
