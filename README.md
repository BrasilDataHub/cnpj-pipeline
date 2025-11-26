# rfb-cnpj-etl

ETL completo dos dados públicos de CNPJ para bancos de dados relacionais.

Fonte: [Dados Abertos CNPJ - Receita Federal](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)

## Sobre

Este projeto pretende facilitar o acesso, extração e estruturação dos dados públicos do CNPJ, disponibilizados
mensalmente pela Receita Federal, permitindo que desenvolvedores, analistas e pesquisadores utilizem essas informações
em bases relacionais para fins analíticos, acadêmicos ou de integração com outros sistemas.
O total de linhas (somando todas as tabelas) já está na casa dos 200 milhões.

## Funcionalidades

- Download completo da base de dados CNPJ no site da RFB
- Preparação e carga completa em banco de dados
- Criação de índices otimizados para melhorar o desempenho das consultas
- Execução por etapas independentes (permite retomar de qualquer ponto)
- Suporte para PostgreSQL

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

### Instalação Simplificada

Se você prefere não executar os comandos manualmente, use o script de automação:

```bash
chmod +x scripts/run.sh
./scripts/run.sh setup
```

Veja em [Scripts](./scripts/) todos os comandos disponíveis.

### Espaço necessário

Cerca de 50GB:

- ~6GB para downloads
- ~40GB do banco de dados (já com índices)

> Os arquivos `.zip` são lidos diretamente, sem extração no disco, o que reduz o uso de espaço temporário.

💡 **Recomenda-se ter ao menos 70 GB livres** para garantir estabilidade durante a execução, especialmente em máquinas
com armazenamento mecânico (HDD).

## Como utilizar

O projeto disponibiliza comandos separados para **download** e **carga de dados**, mas também permite que essas etapas
sejam feitas em conjunto com o comando `complete`.

- Use `complete` para automatizar **todo o processo** (download + carga do mês mais recente disponível).
- Use `download` e `db load` separadamente se quiser maior controle sobre as etapas.

### `complete`

Executa o ciclo completo de **download + carga** para o mês mais recente disponível.

**Comportamento padrão:**

- Baixa o **mês mais recente**
- Mantém 10 downloads simultâneos
- Salva os arquivos no diretório do projeto em `data/downloads`
- Cria e prepara o banco de dados **PostgreSQL** configurado em `config.py`/variáveis de ambiente
- Realiza a carga dos dados
- Cria índices após a carga

```bash
python etl.py complete
```
---

### `download`

Baixa os arquivos `.zip` dos meses desejados da Receita Federal.  
Este comando é utilizado internamente pelo `complete`.

**Comportamento padrão:**

- Baixa o **mês mais recente**
- Salva em `data/downloads`
- **10 downloads simultâneos**
- **Continua** os downloads iniciados anteriormente

```bash
python etl.py download
```

### `db load`

Realiza a carga completa dos dados `.zip` que já estejam baixados.  
Este comando também é usado internamente por `complete`.

**Comportamento padrão:**

- Usa o **mês mais recente**
- Verifica se todos os arquivos `.zip` estão presentes antes de iniciar
- Banco padrão: PostgreSQL (credenciais em `config.py` ou variáveis de ambiente)
- Diretório de dados: `data/downloads/YYYY-MM`
- Executa todas as etapas: carga, patches, PKs, índices e FKs

```bash
python etl.py db load
```

**Carregar apenas os dados (sem etapas adicionais):**

```bash
python etl.py db load --only-data
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

**Exemplo: Retomar a partir da criação de índices**

```bash
python etl.py db index
python etl.py db fk
```

**Exemplo: Fluxo completo etapa por etapa**

```bash
python etl.py db init
python etl.py download --month 11/2025
python etl.py db load --only-data --month 11/2025
python etl.py db patch
python etl.py db pk
python etl.py db index
python etl.py db fk
```

### Logs no terminal

Os logs exibem detalhadamente o progresso de cada etapa (download, validação dos arquivos, preparação do banco de dados,
carga dos dados por arquivo, criação dos índices).

Veja exemplos em [logs.md](docs/logs.md).

### Outros comandos

- Exemplos de uso com **todas as flags disponíveis** estão nos
  arquivos [complete.md](docs/cli/complete.md), [download.md](docs/cli/download.md) e [db_load.md](docs/cli/db_load.md).

- Utilize `python etl.py --help` para ver os comandos e argumentos disponíveis.

## Personalização

Todas as **constantes globais** como diretórios, downloads simultâneos, entre outras, podem ser ajustadas em
`config.py`.

### Chaves primárias, estrangeiras e índices

As definições de chaves primárias, estrangeiras e índices podem ser encontradas em `db/schema.py`.
Edite conforme a sua necessidade.

### Scripts SQL de Otimização

Na pasta `sql/` estão disponíveis scripts para otimizações avançadas:

| Arquivo | Descrição |
|---------|-----------|
| `indexes.sql` | ~50 índices otimizados (BTREE, GIN/pg_trgm, BRIN, HASH) |
| `materialized_views.sql` | 6 views materializadas para estatísticas agregadas |
| `general_improvements.sql` | Extensões (pg_trgm, pg_prewarm) e funções auxiliares |

Esses scripts podem ser executados manualmente após a carga para melhorias adicionais de performance.

## Benchmark de execução

| Processo                     | Tempo                   |
|------------------------------|-------------------------|
| Download dos arquivos        | ~ 01:00:00              |
| Preparação do banco de dados | ~ 00:05:00              |
| Carga de dados completa      | 01:30:00 ~ 02:30:00     |
| Pós-processamento            | ~ 00:15:00              |
| Criação dos índices          | ~ 01:00:00              |
| **Total**                    | **04:00:00 ~ 05:00:00** |

* Utilizando a base de dados de junho de 2025.

> Equipamento: i5-1235U, 16GB RAM, HDD, Windows 11

## Estrutura do Banco de Dados

O modelo relacional do banco de dados pode ser visualizado nos arquivos abaixo:

- [postgres_erd.png](assets/postgres_erd.png): visualização da estrutura relacional das tabelas.
- [postgres_erd.pgerd](assets/postgres_erd.pgerd): arquivo do diagrama exportado pelo pgAdmin.
- [postgres_script.sql](assets/postgres_script.sql): script SQL completo para criação do banco PostgreSQL.

## Exemplos de Consultas

Para começar a explorar os dados, consulte os arquivos de exemplo abaixo. Eles contêm exemplos práticos de como utilizar
as tabelas e colunas para extrair informações úteis, como buscar uma empresa por CNPJ, listar seus sócios ou filtrar
estabelecimentos por cidade.

- Exemplos para PostgreSQL: [query_postgres.md](docs/exemplos/query_postgres.md)

## Estrutura do Projeto

```bash
rfb-cnpj-etl/
├── src/
│   └── rfb_cnpj_etl/
│       ├── etl.py                      # Script principal com argparse
│       ├── orchestrator.py             # Orquestrador de etapas
│       ├── config.py                   # Configurações gerais e constantes
│       ├── cnpj_data/                  # Lógica para download e scraping da base de dados CNPJ
│       │   ├── __init__.py             
│       │   ├── cnpj_public_data.py     # Captura os dados da RFB
│       │   └── cnpj_downloader.py      # Gerencia o download dos arquivos
│       ├── db/                         # Módulos para schema, carga e controle de banco
│       │   ├── __init__.py             
│       │   ├── postgres_builder.py     # Criação do banco de dados (PostgreSQL)
│       │   ├── postgres_loader.py      # Carregamento dos dados no banco (PostgreSQL)
│       │   └── schema.py               # Esquema do banco de dados (tabelas, chaves e índices)
│       └── utils/                      # Funções utilitárias
│           ├── __init__.py
│           ├── logger.py               # Print personalizado com hora e tempo de execução
│           ├── progress.py             # Barra e log de progresso
│           ├── db_transformers.py      # Transformação de dados para o banco
│           └── db_batch_producer.py    # Geração de lotes de dados para carga
├── sql/                                # Scripts SQL para otimizações
│   ├── indexes.sql                     # Índices otimizados (BTREE, GIN, BRIN, HASH)
│   ├── materialized_views.sql          # Views materializadas para estatísticas
│   └── general_improvements.sql        # Extensões e funções auxiliares
├── assets/                             # Dados e arquivos auxiliares
│   ├── cnpj-metadados.pdf              # Dicionário de Dados do Cadastro Nacional da Pessoa Jurídica
│   ├── postgres_script.sql             # Script SQL para criação do banco de dados PostgreSQL
│   ├── database_erd.pgerd              # Diagrama do banco de dados PostgreSQL
│   └── postgres_erd.png                # Imagem do diagrama do banco de dados PostgreSQL
├── data/                               # Diretório padrão para downloads
│   └── downloads/                      # Diretório padrão para downloads
├── docs/                               # Documentação do projeto           
│   ├── exemplos/                       # Exemplos de consultas
│   │   └── query_postgres.md           # Para PostgreSQL
│   ├── cli/                            # Comandos e documentação do CLI
│   │   ├── complete.md                 # Documentação do comando 'complete'
│   │   ├── db_load.md                  # Documentação do comando 'db load'
│   │   └── download.md                 # Documentação do comando 'download'
│   └── normalizacao.md                 # Ajustes realizados nos dados carregados
├── scripts/                            # Automação dos processos (macOS/Linux)      
│   └── run.sh                          # Script unificado (setup, complete, download, load)
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt                    # Dependências do projeto
```

- O projeto suporta apenas PostgreSQL. Para incluir um novo banco de dados (como MySQL), seria necessário:
    - Criar o builder e o loader no diretório `db/`;
    - Adicionar a chamada para o builder e loader no `orchestrator.py`;
    - Ajustar `utils/db_batch_producer.py` se houver diferenças de paralelismo;
    - Acrescentar a nova engine em **ENGINE_OPTIONS** no `config.py` e configurar as variáveis correspondentes.

## Como Contribuir

Contribuições são bem-vindas. Para reportar bugs ou sugerir ideias, por favor, abra
uma [Issue](https://github.com/brasildatahub/rfb-cnpj-etl/issues). Para enviar melhorias no código ou na documentação,
crie um [Pull Request](https://github.com/brasildatahub/rfb-cnpj-etl/pulls).

## Licença

Este projeto está licenciado sob os termos da licença MIT.
Veja o arquivo [LICENSE](../../rfb-cnpj-etl/LICENSE) para mais informações.
