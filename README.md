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
- Criação de índices para melhorar o desempenho das consultas
- Suporte para SQLite e PostgreSQL

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

> Para o `PostgreSQL`, é necessário ter o servidor instalado e configurado. Para o **SQLite**, nenhuma instalação
> adicional é necessária.

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
- Cria e prepara o banco de dados **SQLite** (`data/db/dados_cnpj.db`)
- Realiza a carga dos dados
- Cria índices após a carga

```bash
python cnpj.py complete
```

> Para usar o **PostgreSQL** como padrão, adicione as variáveis (usuário, senha, nome do banco de dados) e altere
> `DEFAULT_ENGINE` para "postgres" em `config.py`.
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
python cnpj.py download
```

### `db load`

Realiza a carga completa dos dados `.zip` que já estejam baixados.  
Este comando também é usado internamente por `complete`.

**Comportamento padrão:**

- Usa o **mês mais recente**
- Verifica se todos os arquivos `.zip` estão presentes antes de iniciar
- Banco padrão: SQLite (`data/db/dados_cnpj.db`)
- Diretório de dados: `data/downloads/YYYY-MM`
- Índices são criados ao final

```bash
python cnpj.py db load
```

### Logs no terminal

Os logs exibem detalhadamente o progresso de cada etapa (download, validação dos arquivos, preparação do banco de dados,
carga dos dados por arquivo, criação dos índices).

Veja exemplos em [logs.md](docs/logs.md).

### Outros comandos

- Exemplos de uso com **todas as flags disponíveis** estão nos
  arquivos [complete.md](docs/cli/complete.md), [download.md](docs/cli/download.md) e [db_load.md](docs/cli/db_load.md).

- Utilize `python cnpj.py --help` para ver os comandos e argumentos disponíveis.

## Personalização

Todas as **constantes globais** como diretórios, downloads simultâneos, entre outras, podem ser ajustadas em
`config.py`.

### Chaves primárias, estrangeiras e índices

As definições de chaves primárias, estrangeiras e índices podem ser encontradas em `db/schema.py`.
Edite conforme a sua necessidade.

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
- Exemplos para SQLite: [query_sqlite.md](docs/exemplos/query_sqlite.md)

## Estrutura do Projeto

```bash
rfb-cnpj-etl/
├── src/
│   └── rfb_cnpj_etl/
│       ├── cnpj.py                     # Script principal com argparse
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
│       │   ├── sqlite_builder.py       # Criação do banco de dados (SQLite)
│       │   ├── sqlite_loader.py        # Carregamento dos dados no banco (SQLite)
│       │   └── schema.py               # Esquema do banco de dados (tabelas, chaves e índices)
│       └── utils/                      # Funções utilitárias
│           ├── __init__.py
│           ├── logger.py               # Print personalizado com hora e tempo de execução
│           ├── progress.py             # Barra e log de progresso
│           ├── db_transformers.py      # Transformação de dados para o banco
│           └── db_batch_producer.py    # Geração de lotes de dados para carga
├── assets/                             # Dados e arquivos auxiliares
│   ├── cnpj-metadados.pdf              # Dicionário de Dados do Cadastro Nacional da Pessoa Jurídica
│   ├── postgres_script.sql             # Script SQL para criação do banco de dados PostgreSQL
│   ├── database_erd.pgerd              # Diagrama do banco de dados PostgreSQL
│   └── postgres_erd.png                # Imagem do diagrama do banco de dados PostgreSQL
├── data/                               # Diretório padrão para o banco de dados (SQLite) e downloads
│   └── downloads/                      # Diretório padrão para downloads
├── docs/                               # Documentação do projeto           
│   ├── exemplos/                       # Exemplos de consultas
│   │   ├── query_postgres.md           # Para PostgreSQL
│   │   └── query_sqlite.md             # Para SQLite
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

- Para incluir um novo banco de dados (como MySQL):
    - Crie o builder e o loader no diretório `db/` e
    - Adicione a chamada para o builder e loader no `orchestrator.py` (no elif)
    - Faça as alterações necessárias em `utils/produce_batches.py` (para paralelismo, por exemplo)
    - Acrescente como opção em **ENGINE_OPTIONS** no `config.py`
    - Adicione as variáveis necessárias em `config.py` (como conexão e diretórios)

## Como Contribuir

Contribuições são bem-vindas. Para reportar bugs ou sugerir ideias, por favor, abra
uma [Issue](https://github.com/brasildatahub/rfb-cnpj-etl/issues). Para enviar melhorias no código ou na documentação,
crie um [Pull Request](https://github.com/brasildatahub/rfb-cnpj-etl/pulls).

## Licença

Este projeto está licenciado sob os termos da licença MIT.
Veja o arquivo [LICENSE](../../rfb-cnpj-etl/LICENSE) para mais informações.
