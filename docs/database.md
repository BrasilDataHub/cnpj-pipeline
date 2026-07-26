# Guia de Estrutura e Consultas PostgreSQL (Base CNPJ)

Este documento serve como referência da **estrutura atual do banco** (tabelas, colunas, índices e materialized views) e traz exemplos de consultas.
A fonte da verdade é o schema programático (`src/rfb_cnpj_etl/db/schema.py`), os índices avançados (`src/rfb_cnpj_etl/db/advanced_indexes.py`) e os SQLs em `sql/materialized_views/`.

> **Recarga destrói objetos derivados**: `db init` e `db load` recriam o schema
> com `DROP TABLE ... CASCADE`, o que remove também as **Materialized Views** e
> a tabela de busca `busca_estabelecimento` — ambas são recriadas nas etapas
> finais do `complete`. A única tabela preservada é a `pipeline_stats`
> (histórico de execuções).

## Estrutura do Banco de Dados

### Tabelas Principais (Carga CNPJ)

#### `empresa`
- **PK:** `cnpj_basico`
- **Colunas:**
  - `cnpj_basico` VARCHAR(8)
  - `razao_social` VARCHAR(200)
  - `cod_natureza_juridica` VARCHAR(4) NOT NULL
  - `cod_qualificacao_responsavel` VARCHAR(2) NOT NULL
  - `capital_social` NUMERIC(16,2) NOT NULL
  - `cod_porte` VARCHAR(2)
  - `ente_federativo_responsavel` VARCHAR(100)
- **FKs:**
  - `cod_natureza_juridica` -> `natureza_juridica.cod_natureza`
  - `cod_qualificacao_responsavel` -> `qualificacao_socio.cod_qualificacao`

#### `estabelecimento`
- **PK:** `cnpj_completo`
- **Colunas:**
  - `cnpj_basico` VARCHAR(8) NOT NULL
  - `cnpj_ordem` VARCHAR(4) NOT NULL
  - `cnpj_dv` VARCHAR(2) NOT NULL
  - `cnpj_completo` CHAR(14) NOT NULL
  - `matriz_filial` VARCHAR(1) NOT NULL
  - `nome_fantasia` VARCHAR(60)
  - `cod_situacao_cadastral` VARCHAR(2) NOT NULL
  - `data_situacao_cadastral` DATE
  - `cod_motivo_situacao_cadastral` VARCHAR(2) NOT NULL
  - `nome_cidade_exterior` VARCHAR(60)
  - `cod_pais` VARCHAR(3)
  - `data_inicio_atividade` DATE NOT NULL
  - `cod_cnae_principal` VARCHAR(7) NOT NULL
  - `cod_cnae_secundario` TEXT
  - `tipo_logradouro` VARCHAR(20)
  - `logradouro` VARCHAR(60)
  - `numero` VARCHAR(6)
  - `complemento` VARCHAR(200)
  - `bairro` VARCHAR(60)
  - `cep` VARCHAR(8)
  - `uf` VARCHAR(2) NOT NULL
  - `cod_municipio` VARCHAR(7)
  - `ddd_telefone_1` VARCHAR(4)
  - `telefone_1` VARCHAR(10)
  - `ddd_telefone_2` VARCHAR(4)
  - `telefone_2` VARCHAR(10)
  - `ddd_fax` VARCHAR(4)
  - `fax` VARCHAR(10)
  - `email` TEXT
  - `situacao_especial` VARCHAR(100)
  - `data_situacao_especial` DATE
  - `cod_regiao_ibge` INTEGER
  - `cod_estado_ibge` INTEGER
  - `cod_cidade_ibge` INTEGER
- **FKs:**
  - `cnpj_basico` -> `empresa.cnpj_basico`
  - `cod_cnae_principal` -> `cnae.cod_cnae`
  - `cod_pais` -> `pais.cod_pais`
  - `cod_motivo_situacao_cadastral` -> `motivo.cod_motivo`
  - `cod_regiao_ibge` -> `ibge_regiao.cod_regiao_ibge`
  - `cod_estado_ibge` -> `ibge_estado.cod_estado_ibge`
  - `cod_cidade_ibge` -> `ibge_cidade.cod_cidade_ibge`

#### `socio`
- **PK:** sem PK explícita
- **Colunas:**
  - `cnpj_basico` VARCHAR(8) NOT NULL
  - `identificador_socio` VARCHAR(1) NOT NULL
  - `nome_socio` VARCHAR(200)
  - `cnpj_cpf_socio` VARCHAR(14)
  - `cod_qualificacao_socio` VARCHAR(2) NOT NULL
  - `data_entrada_sociedade` DATE NOT NULL
  - `cod_pais` VARCHAR(3)
  - `cpf_representante_legal` VARCHAR(11)
  - `nome_representante_legal` VARCHAR(100)
  - `cod_qualificacao_representante_legal` VARCHAR(2)
  - `cod_faixa_etaria` VARCHAR(1) NOT NULL
- **FKs:**
  - `cnpj_basico` -> `empresa.cnpj_basico`
  - `cod_pais` -> `pais.cod_pais`
  - `cod_qualificacao_socio` -> `qualificacao_socio.cod_qualificacao`
  - `cod_qualificacao_representante_legal` -> `qualificacao_socio.cod_qualificacao`

#### `simples`
- **PK:** `cnpj_basico`
- **Colunas:**
  - `cnpj_basico` VARCHAR(8)
  - `opcao_simples` VARCHAR(1)
  - `data_opcao_simples` DATE
  - `data_exclusao_simples` DATE
  - `opcao_mei` VARCHAR(1)
  - `data_opcao_mei` DATE
  - `data_exclusao_mei` DATE
- **FKs:**
  - `cnpj_basico` -> `empresa.cnpj_basico`

#### `estabelecimento_cnae_sec`
- **PK:** sem PK explícita
- **Colunas:**
  - `cnpj_completo` CHAR(14) NOT NULL
  - `cod_cnae` VARCHAR(7) NOT NULL
  - `cod_regiao_ibge` SMALLINT
  - `cod_estado_ibge` SMALLINT
  - `cod_cidade_ibge` INTEGER
  - `data_inicio_atividade` DATE
- **FKs:**
  - `cnpj_completo` -> `estabelecimento.cnpj_completo`
  - `cod_cnae` -> `cnae.cod_cnae`

### Tabela de Busca (derivada, recriada a cada carga)

#### `busca_estabelecimento`
Tabela desnormalizada enxuta para a busca do website (fonte:
`src/rfb_cnpj_etl/db/search_table.py`, comando `db search`). Uma linha por
estabelecimento; nomes normalizados com `unaccent(upper(...))`. Recriada a
cada carga mensal por build-and-swap (`*_new` + `RENAME` em transação única).
- **PK:** `cnpj_completo`
- **Colunas:**
  - `cnpj_completo` CHAR(14)
  - `cnpj_basico` VARCHAR(8)
  - `razao_social_norm` TEXT — `unaccent(upper(empresa.razao_social))`
  - `nome_fantasia_norm` TEXT — `unaccent(upper(estabelecimento.nome_fantasia))`
  - `cod_regiao_ibge` / `cod_estado_ibge` / `cod_cidade_ibge` INTEGER
  - `cod_cnae_principal` VARCHAR(7)
  - `cod_situacao_cadastral` VARCHAR(2)
  - `matriz_filial` VARCHAR(1)
  - `cod_porte` VARCHAR(2)
  - `cod_natureza_juridica` VARCHAR(4)
  - `data_inicio_atividade` DATE
  - `cep` VARCHAR(8)
  - `ddd_telefone_1` / `ddd_telefone_2` VARCHAR(4) — os dois DDDs: o filtro
    de DDD do website sempre casou contra ambos os telefones
  - `bairro_norm` TEXT — `unaccent(upper(estabelecimento.bairro))`
- **Índices:**
  - `idx_busca_razao_social_trgm` GIN (`razao_social_norm` gin_trgm_ops)
  - `idx_busca_nome_fantasia_trgm` GIN (`nome_fantasia_norm` gin_trgm_ops)
  - `idx_busca_cidade_situacao_cnpj` (`cod_cidade_ibge`, `cod_situacao_cadastral`, `cnpj_completo`)
  - `idx_busca_cnae_estado_situacao` (`cod_cnae_principal`, `cod_estado_ibge`, `cod_situacao_cadastral`)
- **Sem FKs** (por design: a troca atômica por RENAME não pode depender de
  constraints cruzadas; a consistência vem da recriação conjunta na carga).

### Tabelas de Domínio (Lookup)

#### `cnae`
- **PK:** `cod_cnae`
- **Colunas:** `cod_cnae` VARCHAR(7), `nome_cnae` VARCHAR(200) NOT NULL

#### `natureza_juridica`
- **PK:** `cod_natureza`
- **Colunas:** `cod_natureza` VARCHAR(4), `nome_natureza` VARCHAR(200) NOT NULL

#### `qualificacao_socio`
- **PK:** `cod_qualificacao`
- **Colunas:** `cod_qualificacao` VARCHAR(2), `nome_qualificacao` VARCHAR(200) NOT NULL

#### `motivo`
- **PK:** `cod_motivo`
- **Colunas:** `cod_motivo` VARCHAR(2), `nome_motivo` VARCHAR(100) NOT NULL

#### `pais`
- **PK:** `cod_pais`
- **Colunas:** `cod_pais` VARCHAR(3), `nome_pais` VARCHAR(60) NOT NULL

> A tabela pode conter **linhas sintéticas**: além dos ~18 países extras
> conhecidos que o `db patch` insere, qualquer código presente em
> `estabelecimento`/`socio` mas ausente do PAISCSV do mês é absorvido
> automaticamente com o nome `CODIGO NAO CONSTANTE NA TABELA RFB` — sem isso,
> as FKs de país falhariam (incidente de 07/2026).

#### `municipio_rfb`
- **PK:** `cod_municipio`
- **Colunas:** `cod_municipio` VARCHAR(7), `nome_municipio` VARCHAR(120) NOT NULL

### Tabelas IBGE (Enriquecimento)

#### `ibge_regiao`
- **PK:** `cod_regiao_ibge`
- **Colunas:**
  - `cod_regiao_ibge` INTEGER
  - `sigla_regiao` VARCHAR(2) UNIQUE
  - `nome_regiao` VARCHAR(50) NOT NULL

#### `ibge_estado`
- **PK:** `cod_estado_ibge`
- **Colunas:**
  - `cod_estado_ibge` INTEGER
  - `sigla_uf` VARCHAR(2) UNIQUE NOT NULL
  - `nome_estado` VARCHAR(100) NOT NULL
  - `latitude` NUMERIC(9,6)
  - `longitude` NUMERIC(9,6)
  - `cod_regiao_ibge` INTEGER NOT NULL
- **FKs:** `cod_regiao_ibge` -> `ibge_regiao.cod_regiao_ibge`

#### `ibge_cidade`
- **PK:** `cod_cidade_ibge`
- **Colunas:**
  - `cod_cidade_ibge` INTEGER
  - `nome_cidade` VARCHAR(120) NOT NULL
  - `latitude` NUMERIC(9,6)
  - `longitude` NUMERIC(9,6)
  - `capital` BOOLEAN
  - `cod_estado_ibge` INTEGER NOT NULL
  - `cod_municipio` VARCHAR(7) UNIQUE
  - `ddd` VARCHAR(3)
  - `fuso_horario` VARCHAR(50)
- **FKs:** `cod_estado_ibge` -> `ibge_estado.cod_estado_ibge`

---

## Índices

### Índices Básicos (criados pelo ETL)

#### `empresa`
- `idx_empresa_cnpj` (`cnpj_basico`)
- `idx_empresa_razao_social` (`razao_social`)
- `idx_empresa_natureza` (`cod_natureza_juridica`)
- `idx_empresa_porte` (`cod_porte`)

#### `estabelecimento`
- `idx_estab_empresa` (`cnpj_basico`)
- `idx_estab_nome_fantasia` (`nome_fantasia`)
- `idx_estab_data_situacao` (`data_situacao_cadastral`)
- `idx_estab_municipio` (`cod_municipio`)
- `idx_estab_uf_municipio` (`uf`, `cod_municipio`)

> Seis índices simples (`idx_estab_cnae_principal`, `idx_estab_data_inicio`,
> `idx_estab_situacao`, `idx_estab_regiao_ibge`, `idx_estab_estado_ibge`,
> `idx_estab_cidade_ibge`) foram **removidos em 2026-07** por serem prefixos
> exatos de índices compostos existentes ou terem seletividade baixa demais.
> Justificativas e protocolo de remoção em produção: [index_cleanup](index_cleanup.md).

#### `simples`
- `idx_simples_opcoes` (`opcao_simples`, `opcao_mei`, `cnpj_basico`)

#### `socio`
- `idx_socio_empresa` (`cnpj_basico`)
- `idx_socio_cpf_cnpj` (`cnpj_cpf_socio`)
- `idx_socio_nome` (`nome_socio`)

#### `estabelecimento_cnae_sec`
- `idx_estab_cnae_sec_cnpj` (`cnpj_completo`)

#### `ibge_regiao`
- `idx_ibge_regiao_sigla` (`sigla_regiao`)

#### `ibge_estado`
- `idx_ibge_estado_sigla` (`sigla_uf`)
- `idx_ibge_estado_regiao` (`cod_regiao_ibge`)

#### `ibge_cidade`
- `idx_ibge_cidade_estado` (`cod_estado_ibge`)
- `idx_ibge_cidade_municipio` (`cod_municipio`)

### Índices Avançados (automáticos, via `advanced_indexes.py`)

Criados automaticamente pelo `db index` (e pelo `db load`/`complete` sem
`--skip-index`), junto com os básicos. São 40 índices, construídos **sem**
`CONCURRENTLY` e em paralelo (ver [Configuração](configuration.md#criação-de-índices)).

#### `estabelecimento`
- `idx_estab_cep` (`cep`)
- `idx_estab_regiao_estado` (`cod_regiao_ibge`, `cod_estado_ibge`)
- `idx_estab_cnae_estado` (`cod_cnae_principal`, `cod_estado_ibge`)
- `idx_estab_cnae_cidade` (`cod_cnae_principal`, `cod_cidade_ibge`)
- `idx_estab_data_inicio_brin` BRIN (`data_inicio_atividade`) WITH (pages_per_range = 32)
- `idx_estab_ativas` (`cod_situacao_cadastral`) WHERE `cod_situacao_cadastral = '02'`
- `idx_estab_cidade_ativas_cnpj` (`cod_cidade_ibge`, `cnpj_completo`) WHERE `cod_situacao_cadastral = '02'`
- `idx_estab_estado_ativas_cnpj` (`cod_estado_ibge`, `cnpj_completo`) WHERE `cod_situacao_cadastral = '02'`
- `idx_estab_regiao_ativas_cnpj` (`cod_regiao_ibge`, `cnpj_completo`) WHERE `cod_situacao_cadastral = '02'`
- `idx_estab_nome_fantasia_trgm` GIN (`nome_fantasia gin_trgm_ops`)
- `idx_estab_email` (`email`) WHERE `email IS NOT NULL AND email != ''`
- `idx_estab_telefone` (`telefone_1`) WHERE `telefone_1 IS NOT NULL AND telefone_1 != ''`
- `idx_estab_email_hash` HASH (`email`)
- `idx_estab_prospeccao` (`cod_estado_ibge`, `cod_cnae_principal`, `cod_situacao_cadastral`)
- `idx_estab_local_cnae` (`cod_cidade_ibge`, `cod_cnae_principal`, `matriz_filial`)
- `idx_estab_novos_estado` (`data_inicio_atividade`, `cod_estado_ibge`)
- `idx_estab_leads_email` (`cod_cnae_principal`, `cod_situacao_cadastral`) WHERE `email IS NOT NULL AND email != ''`
- `idx_estab_temporal` (`cod_estado_ibge`, `cod_cnae_principal`, `data_inicio_atividade`)
- `idx_estab_cidade_situacao_cnpj` (`cod_cidade_ibge`, `cod_situacao_cadastral`, `cnpj_completo`)
- `idx_estab_estado_situacao_cnpj` (`cod_estado_ibge`, `cod_situacao_cadastral`, `cnpj_completo`)
- `idx_estab_cidade_cnae_situacao` (`cod_cidade_ibge`, `cod_cnae_principal`, `cod_situacao_cadastral`)
- `idx_estab_estado_cnae_situacao` (`cod_estado_ibge`, `cod_cnae_principal`, `cod_situacao_cadastral`)
- `idx_estab_ddd1_covering` (`ddd_telefone_1`) INCLUDE (`cnpj_completo`, `cod_cidade_ibge`) WHERE `ddd_telefone_1 IS NOT NULL AND ddd_telefone_1 != ''`
- `idx_estab_bairro_trgm` GIN (`bairro gin_trgm_ops`)
- `idx_estab_email_prospeccao` (`cod_cidade_ibge`, `cnpj_completo`) WHERE `email IS NOT NULL AND email != '' AND email NOT ILIKE '%contab%'`
- `idx_estab_estado_matriz_cnpj` (`cod_estado_ibge`, `matriz_filial`, `cnpj_basico`) — sitemaps: empresas por UF
- `idx_estab_estado_cnae` (`cod_estado_ibge`, `cod_cnae_principal`) — sitemaps: agregação CNAE por estado
- `idx_estab_estado_cidade_cnae` (`cod_estado_ibge`, `cod_cidade_ibge`, `cod_cnae_principal`) — sitemaps: agregação CNAE por cidade

> Removidos em 2026-07 (duplicatas com collation C ou sem ganho mensurável):
> `idx_estab_ddd`, `idx_estab_matriz_filial`, `idx_estab_nome_fantasia_prefix`,
> `idx_estab_cnpj_completo_hash`, `idx_empresa_razao_social_prefix`,
> `idx_socio_nome_prefix`. Ver [index_cleanup](index_cleanup.md).

#### `empresa`
- `idx_empresa_capital` (`capital_social`)
- `idx_empresa_razao_social_trgm` GIN (`razao_social gin_trgm_ops`)
- `idx_empresa_porte_cnpj` (`cod_porte`, `cnpj_basico`)
- `idx_empresa_natureza_cnpj` (`cod_natureza_juridica`, `cnpj_basico`)

#### `estabelecimento_cnae_sec`
- `idx_cnae_sec_cnae` (`cod_cnae`)
- `idx_cnae_sec_covering` (`cod_cnae`) INCLUDE (`cnpj_completo`)
- `idx_cnae_sec_cnae_estado` (`cod_cnae`, `cod_estado_ibge`)
- `idx_cnae_sec_cnae_cidade` (`cod_cnae`, `cod_cidade_ibge`)
- `idx_cnae_sec_cnae_regiao` (`cod_cnae`, `cod_regiao_ibge`)

#### `socio`
- `idx_socio_nome_trgm` GIN (`nome_socio gin_trgm_ops`)

#### `simples`
- `idx_simples_opcao_simples` (`cnpj_basico`) WHERE `opcao_simples = 'S'`
- `idx_simples_opcao_mei` (`cnpj_basico`) WHERE `opcao_mei = 'S'`

---

## Materialized Views

### Âncora temporal e semântica de períodos

Tudo que é "período" nas MVs é ancorado em **`fn_mes_ancora()`**
(`sql/materialized_views/00_helpers.sql`), nunca em `CURRENT_DATE`.

`fn_mes_ancora()` devolve o **primeiro dia do último mês completo da carga**:
carga de referência `2026-07` ⇒ âncora `2026-06-01`. São dois braços
combinados por `LEAST` (o mais conservador vence), porque cada um cobre a falha
do outro:

| Braço | Origem | Falha quando |
|-------|--------|--------------|
| 1 | `pipeline_stats.reference_period` − 1 mês | tabela ainda não existe, ou as MVs são recriadas sem recarregar os dados |
| 2 | `date_trunc('month', max(data_inicio_atividade))` − 1 mês | a RFB publica registros com data futura (existem CNPJs com abertura em 2099) |

Motivo de existir: com `CURRENT_DATE`, rodar a mesma MV dois dias seguidos
produzia recortes diferentes, e o último mês da série era **sempre parcial** —
comparar um mês incompleto com um mês fechado gera queda artificial em todo
lugar.

**Convenção de janelas** (meses de calendário, `N` = `periodo_meses`):

```
atual    = [ancora − (N−1) meses  ..  ancora]
anterior = [ancora − (2N−1) meses ..  ancora − N meses]
```

As colunas `periodo_*_fim` guardam o **último mês incluso**, não o limite
exclusivo. Com âncora `2026-06` e `N = 6`: atual `2026-01..2026-06`, anterior
`2025-07..2025-12`.

**Convenções de valor:**

| Situação | Resultado |
|----------|-----------|
| `anterior = 0` | `*_variacao_pct` = `NULL` (o site renderiza "—" ou "Novo") |
| `aberturas = 0` | `sobrevivencia_*_pct` = `NULL` (0/0 não é 0%) |
| um dos lados `NULL` | `sobrevivencia_delta_pp` = `NULL` |
| variação absurda | `*_variacao_pct` satura em ±999999,99 para caber em `numeric(8,2)` |

**Semântica de "aberturas"** (padronizada nesta versão): *toda* abertura do
período (`data_inicio_atividade` na janela), independentemente da situação
cadastral atual. Quem "abriu e continua ativo" é a coluna separada
`ainda_ativos`, base da taxa de sobrevivência.

> **Viés de idade das coortes.** A coorte do período atual é mais jovem que a do
> anterior e teve menos tempo para fechar, então a sobrevivência do período
> atual é **estruturalmente maior**. `sobrevivencia_delta_pp` é indicador de
> tendência, não medida de qualidade das empresas.

**Mês parcial.** As séries mensais trazem `mes_parcial = (mes > mes_ancora)`.
Esses meses aparecem na série (sinalizados, para o gráfico tracejar o trecho em
consolidação) mas ficam **fora** das MVs de comparativo.

**Baixas.** Situação cadastral `'08'` contada pelo mês de
`data_situacao_cadastral`. `saldo = aberturas − baixas`.

> ### ⚠️ Breaking change: colunas `novos_*`
>
> Em `mv_stats_estado`, `mv_stats_municipio`, `mv_stats_cnae` e
> `mv_top_cnaes_cidade` as colunas `novos_*` **mudaram de valor**:
>
> 1. as janelas passaram de `CURRENT_DATE` para a âncora — o recorte agora
>    termina no último mês completo, e não "hoje";
> 2. o filtro `cod_situacao_cadastral = '02'` **saiu** de `mv_stats_estado`,
>    `mv_stats_municipio` e `mv_top_cnaes_cidade` (em `mv_stats_cnae` ele já não
>    existia). "Novos" agora conta todas as aberturas.
>
> Os números exibidos pelo site mudam nas duas pontas: para baixo pelo recorte
> mais curto, para cima pela remoção do filtro de situação. A divergência entre
> MVs — territoriais filtravam ativas, CNAE não — é justamente o que essa
> padronização elimina.

### Sentinela `0` nas MVs de comparativo

As três MVs de comparativo têm uma coluna `nivel` e **só a coluna geográfica
daquele nível é preenchida**; as demais recebem `0` (nunca `NULL`, que não
indexa nem compara bem, e obrigaria `IS NULL` espalhado pelo site):

| `nivel` | `cod_regiao_ibge` | `cod_estado_ibge` | `cod_cidade_ibge` |
|---------|-------------------|-------------------|-------------------|
| `brasil` | 0 | 0 | 0 |
| `regiao` | código da região | 0 | 0 |
| `estado` | 0 | código da UF | 0 |
| `municipio` | 0 | 0 | código do município |

Consulta típica do site: `WHERE nivel = 'estado' AND cod_estado_ibge = 35` —
sem `IS NULL`, coberta pelo índice único.

Em `mv_comparativo_cnae` e `mv_comparativo_natureza` não existe o nível
`municipio` (as séries de origem param em UF) e `cod_cidade_ibge` é **sempre 0**;
a coluna existe só para as três MVs terem o mesmo shape.

**Grade completa:** toda entidade presente nos últimos 96 meses ganha as 5
linhas de período, mesmo zeradas — assim o site nunca precisa distinguir "não
houve abertura" de "a MV não tem essa linha".

### As 19 Materialized Views

| # | View | Grão | Depende de |
|---|------|------|------------|
| 01 | `mv_stats_estado` | estado | — |
| 02 | `mv_stats_municipio` | município | — |
| 03 | `mv_stats_cnae` | CNAE | — |
| 04 | `mv_stats_cnae_estado` | CNAE × UF | — |
| 05 | `mv_abertura_periodo` | mês × município | — |
| 06 | `mv_top_cnaes_cidade` | CNAE × município | — |
| 07 | `mv_stats_cidade_situacao` | município × situação | — |
| 08 | `mv_regime_tributario_cidade` | município × situação | — |
| 09 | `mv_porte_cidade` | município × porte × situação | — |
| 10 | `mv_stats_natureza_juridica_estado` | natureza × UF | — |
| 11 | `mv_stats_natureza_juridica_municipio` | natureza × município | — |
| 12 | `mv_stats_natureza_juridica` | natureza | — |
| 13 | `mv_stats_natureza_juridica_cnae` | natureza × CNAE | — |
| 14 | `mv_movimentacao_mensal_cnae` | mês × CNAE × UF | — |
| 15 | `mv_movimentacao_mensal_natureza` | mês × natureza × UF | — |
| 16 | `mv_movimentacao_mensal_porte` | mês × porte × UF | — |
| 17 | `mv_comparativo_territorio` | território × período | 05 |
| 18 | `mv_comparativo_cnae` | CNAE × nível × período | 14 |
| 19 | `mv_comparativo_natureza` | natureza × nível × período | 15 |

O `00_helpers.sql` não cria MV: define `fn_mes_ancora()`, `fn_variacao_pct()` e
`fn_sobrevivencia_pct()`, usadas pelas demais.

### Views Disponíveis e Estrutura

#### `mv_stats_estado`
- **Colunas:**
  - `cod_estado_ibge`, `sigla_uf`, `nome_estado`, `cod_regiao_ibge`
  - `total_estabelecimentos`, `ativos`, `matrizes_ativas`, `filiais_ativas`, `total_empresas`
  - `novos_1mes`, `novos_6meses`, `novos_1ano`, `novos_2anos`, `novos_4anos`, `mes_ancora`
- **Índices:**
  - `idx_mv_stats_estado_pk` UNIQUE (`cod_estado_ibge`)
  - `idx_mv_stats_estado_regiao` (`cod_regiao_ibge`)
  - `idx_mv_stats_estado_sigla` (`sigla_uf`)

#### `mv_stats_municipio`
- **Colunas:**
  - `cod_cidade_ibge`, `nome_cidade`, `cod_estado_ibge`, `sigla_uf`, `cod_regiao_ibge`
  - `total_estabelecimentos`, `ativos`, `matrizes_ativas`, `filiais_ativas`, `total_empresas`
  - `primeira_abertura`, `ultima_abertura`
  - `novos_1mes`, `novos_6meses`, `novos_1ano`, `novos_2anos`, `novos_4anos`, `mes_ancora`
- **Índices:**
  - `idx_mv_stats_municipio_pk` UNIQUE (`cod_cidade_ibge`)
  - `idx_mv_stats_municipio_estado` (`cod_estado_ibge`)
  - `idx_mv_stats_municipio_regiao` (`cod_regiao_ibge`)
  - `idx_mv_stats_municipio_sigla` (`sigla_uf`)

#### `mv_stats_cnae`
- **Colunas:**
  - `cod_cnae_principal`, `nome_cnae`
  - `total_estabelecimentos`, `ativos`, `estados_presentes`, `cidades_presentes`
  - `matrizes`, `com_email`, `com_telefone`
  - `novos_1mes`, `novos_6meses`, `novos_1ano`, `novos_2anos`, `novos_4anos`, `mes_ancora`
- **Índices:**
  - `idx_mv_stats_cnae_pk` UNIQUE (`cod_cnae_principal`)
  - `idx_mv_stats_cnae_total` (`total_estabelecimentos` DESC)

#### `mv_stats_cnae_estado`
- **Colunas:**
  - `cod_cnae_principal`, `cod_estado_ibge`, `total`, `ativos`, `com_email`, `matrizes`
- **Índices:**
  - `idx_mv_stats_cnae_estado_pk` UNIQUE (`cod_cnae_principal`, `cod_estado_ibge`)
  - `idx_mv_stats_cnae_estado_cnae` (`cod_cnae_principal`)
  - `idx_mv_stats_cnae_estado_estado` (`cod_estado_ibge`)

#### `mv_abertura_periodo`
- **Colunas:**
  - `mes_abertura`, `cod_cidade_ibge`, `cod_estado_ibge`, `cod_regiao_ibge`
  - `total_aberturas`, `empresas_unicas`, `ainda_ativos`, `baixas`, `saldo`
  - `mes_ancora`, `mes_parcial`
- **Índices:**
  - `idx_mv_abertura_pk` UNIQUE (`mes_abertura`, `cod_cidade_ibge`, `cod_estado_ibge`)
  - `idx_mv_abertura_mes` (`mes_abertura`)
  - `idx_mv_abertura_estado` (`cod_estado_ibge`)
  - `idx_mv_abertura_periodo_cidade` (`cod_cidade_ibge`, `mes_abertura`)
  - `idx_mv_abertura_regiao` (`cod_regiao_ibge`, `mes_abertura`)
- **Nota:** granularidade municipal; agregados estaduais somam as cidades. Linhas com
  `cod_cidade_ibge` NULL (estabelecimento sem correspondência IBGE) agrupam por estado —
  por isso o índice único inclui `cod_estado_ibge`.
- **Nota:** `mes_abertura` é o **mês da movimentação** (vale para aberturas e para
  baixas). O nome legado foi mantido por compatibilidade com o Model do site.
  Um mês pode ter baixa sem ter tido abertura.
- **Atenção:** `empresas_unicas` é `COUNT(DISTINCT cnpj_basico)` **dentro do município**.
  Somar cidades superconta empresas com estabelecimentos em mais de um município;
  para deduplicação exata em recortes maiores, consulte a tabela base.

#### `mv_top_cnaes_cidade`
- **Colunas:**
  - `cod_cidade_ibge`, `cod_cnae_principal`, `nome_cnae`, `cod_estado_ibge`
  - `total`, `ativos`, `novos_1mes`, `novos_6meses`, `novos_1ano`, `mes_ancora`, `ranking`
- **Índices:**
  - `idx_mv_top_cnaes_cidade_pk` UNIQUE (`cod_cidade_ibge`, `cod_cnae_principal`)
  - `idx_mv_top_cnaes_cidade_cidade` (`cod_cidade_ibge`)
  - `idx_mv_top_cnaes_cidade_cnae` (`cod_cnae_principal`)
  - `idx_mv_top_cnaes_cidade_estado` (`cod_estado_ibge`)
  - `idx_mv_top_cnaes_cidade_ranking` (`cod_cidade_ibge`, `ranking`)
  - `idx_mv_top_cnaes_cidade_ativos` (`ativos` DESC)

#### `mv_stats_cidade_situacao`
- **Colunas:**
  - `cod_cidade_ibge`, `cod_estado_ibge`, `cod_regiao_ibge`, `cod_situacao_cadastral`
  - `total`, `matrizes`, `com_email`, `com_telefone`, `email_prospeccao`
- **Índices:**
  - `idx_mv_stats_cidade_situacao_pk` UNIQUE (`cod_cidade_ibge`, `cod_situacao_cadastral`)
  - `idx_mv_stats_cidade_situacao_cidade` (`cod_cidade_ibge`)
  - `idx_mv_stats_cidade_situacao_situacao` (`cod_situacao_cadastral`)
  - `idx_mv_stats_cidade_situacao_estado` (`cod_estado_ibge`, `cod_situacao_cadastral`)
  - `idx_mv_stats_cidade_situacao_regiao` (`cod_regiao_ibge`, `cod_situacao_cadastral`)

#### `mv_regime_tributario_cidade`
- **Colunas:**
  - `cod_cidade_ibge`, `cod_estado_ibge`, `cod_regiao_ibge`, `cod_situacao_cadastral`
  - `total`, `simples_nacional`, `mei`, `simples_ou_mei`, `lucro_presumido_real`
- **Índices:**
  - `idx_mv_regime_tributario_pk` UNIQUE (`cod_cidade_ibge`, `cod_situacao_cadastral`)
  - `idx_mv_regime_tributario_estado` (`cod_estado_ibge`, `cod_situacao_cadastral`)
  - `idx_mv_regime_tributario_regiao` (`cod_regiao_ibge`, `cod_situacao_cadastral`)

#### `mv_porte_cidade`
- **Colunas:**
  - `cod_cidade_ibge`, `cod_estado_ibge`, `cod_regiao_ibge`, `cod_porte`, `cod_situacao_cadastral`
  - `total`, `matrizes`, `com_email`
- **Índices:**
  - `idx_mv_porte_cidade_pk` UNIQUE (`cod_cidade_ibge`, `cod_porte`, `cod_situacao_cadastral`)
  - `idx_mv_porte_cidade_estado` (`cod_estado_ibge`, `cod_porte`, `cod_situacao_cadastral`)
  - `idx_mv_porte_cidade_regiao` (`cod_regiao_ibge`, `cod_situacao_cadastral`, `cod_porte`)
  - `idx_mv_porte_cidade_porte` (`cod_porte`, `cod_situacao_cadastral`)

#### `mv_stats_natureza_juridica_estado`
- **Colunas:**
  - `cod_natureza`, `cod_regiao_ibge`, `cod_estado_ibge`, `total`, `ativos`
- **Índices:**
  - `idx_mv_nj_est_pk` UNIQUE (`cod_natureza`, `cod_estado_ibge`)
  - `idx_mv_nj_est_natureza` (`cod_natureza`)
  - `idx_mv_nj_est_regiao` (`cod_regiao_ibge`)
  - `idx_mv_nj_est_estado` (`cod_estado_ibge`)
  - `idx_mv_nj_est_total` (`total` DESC)
  - `idx_mv_nj_est_ativos` (`ativos` DESC)
  - `idx_mv_nj_est_regiao_total` (`cod_regiao_ibge`, `total` DESC)
  - `idx_mv_nj_est_nat_total` (`cod_natureza`, `total` DESC)

#### `mv_stats_natureza_juridica_municipio`
- **Colunas:**
  - `cod_natureza`, `cod_estado_ibge`, `cod_cidade_ibge`, `total`, `ativos`
- **Índices:**
  - `idx_mv_nj_mun_pk` UNIQUE (`cod_natureza`, `cod_cidade_ibge`)
  - `idx_mv_nj_mun_natureza` (`cod_natureza`)
  - `idx_mv_nj_mun_estado` (`cod_estado_ibge`)
  - `idx_mv_nj_mun_cidade` (`cod_cidade_ibge`)
  - `idx_mv_nj_mun_total` (`total` DESC)
  - `idx_mv_nj_mun_ativos` (`ativos` DESC)
  - `idx_mv_nj_mun_estado_total` (`cod_estado_ibge`, `total` DESC)

#### `mv_stats_natureza_juridica`
- **Colunas:**
  - `cod_natureza`, `nome_natureza`, `total`, `ativos`
- **Índices:**
  - `idx_mv_natureza_cod` UNIQUE (`cod_natureza`)
  - `idx_mv_natureza_total` (`total` DESC)
  - `idx_mv_natureza_ativos` (`ativos` DESC)
  - `idx_mv_natureza_nome` (`nome_natureza`)
  - `idx_mv_natureza_nome_trgm` GIN (`nome_natureza gin_trgm_ops`)

#### `mv_stats_natureza_juridica_cnae`
- **Colunas:**
  - `cod_natureza`, `cod_cnae`, `total`
- **Índices:**
  - `idx_mv_nj_cnae_pk` UNIQUE (`cod_natureza`, `cod_cnae`)
  - `idx_mv_nj_cnae_cnae` (`cod_cnae`)
  - `idx_mv_nj_cnae_total` (`total` DESC)
  - `idx_mv_nj_cnae_natureza_total` (`cod_natureza`, `total` DESC)
- **Nota:** o índice único é novo. Era a única MV sem ele, o que forçava
  `REFRESH` sem `CONCURRENTLY` e bloqueava leituras do site durante todo o
  rebuild. Substituiu o antigo `idx_mv_nj_cnae_natureza`, do qual é prefixo.

#### `mv_movimentacao_mensal_cnae`
- **Colunas:**
  - `mes_abertura`, `cod_cnae_principal`, `cod_estado_ibge`, `cod_regiao_ibge`
  - `aberturas`, `ainda_ativos`, `baixas`, `saldo`, `mes_ancora`, `mes_parcial`
- **Índices:**
  - `idx_mv_mov_cnae_pk` UNIQUE (`mes_abertura`, `cod_cnae_principal`, `cod_estado_ibge`)
  - `idx_mv_mov_cnae_cnae` (`cod_cnae_principal`, `mes_abertura`)
  - `idx_mv_mov_cnae_estado` (`cod_estado_ibge`, `mes_abertura`)
  - `idx_mv_mov_cnae_regiao` (`cod_regiao_ibge`, `mes_abertura`)
- **Nota:** nacional = soma das UFs; regional = soma das UFs da região. Não desce
  a município: `mês × CNAE × 5.570 municípios` daria centenas de milhões de linhas.
- **Nota:** sem `empresas_unicas` — `COUNT(DISTINCT cnpj_basico)` por CNAE não é
  somável entre CNAEs (a mesma empresa tem estabelecimentos com CNAEs diferentes).

#### `mv_movimentacao_mensal_natureza`
- **Colunas:**
  - `mes_abertura`, `cod_natureza`, `cod_estado_ibge`, `cod_regiao_ibge`
  - `aberturas`, `ainda_ativos`, `baixas`, `saldo`, `mes_ancora`, `mes_parcial`
- **Índices:**
  - `idx_mv_mov_natureza_pk` UNIQUE (`mes_abertura`, `cod_natureza`, `cod_estado_ibge`)
  - `idx_mv_mov_natureza_natureza` (`cod_natureza`, `mes_abertura`)
  - `idx_mv_mov_natureza_estado` (`cod_estado_ibge`, `mes_abertura`)
  - `idx_mv_mov_natureza_regiao` (`cod_regiao_ibge`, `mes_abertura`)
- **Atenção:** conta **estabelecimentos**, não CNPJs básicos distintos — ao
  contrário de `mv_stats_natureza_juridica*` (10/11/12), que contam matrizes
  distintas. Isso mantém as séries mensais somáveis entre si.

#### `mv_movimentacao_mensal_porte`
- **Colunas:**
  - `mes_abertura`, `cod_porte`, `cod_estado_ibge`, `cod_regiao_ibge`
  - `aberturas`, `ainda_ativos`, `baixas`, `saldo`, `mes_ancora`, `mes_parcial`
- **Índices:**
  - `idx_mv_mov_porte_pk` UNIQUE (`mes_abertura`, `cod_porte`, `cod_estado_ibge`)
  - `idx_mv_mov_porte_porte` (`cod_porte`, `mes_abertura`)
  - `idx_mv_mov_porte_estado` (`cod_estado_ibge`, `mes_abertura`)
  - `idx_mv_mov_porte_regiao` (`cod_regiao_ibge`, `mes_abertura`)
- **Atenção:** `cod_porte` é o porte **atual** da empresa, não o da data de
  abertura (a RFB não versiona o campo). A série responde "das empresas que hoje
  são ME, quantas abriram em cada mês", e não "quantas ME foram abertas em cada
  mês". Por isso não existe MV de comparativo por porte.

#### `mv_comparativo_territorio`
- **Colunas:**
  - `nivel`, `cod_regiao_ibge`, `cod_estado_ibge`, `cod_cidade_ibge`
  - `periodo_meses` (`smallint`: 1, 6, 12, 24, 48), `mes_ancora`
  - `periodo_atual_inicio`, `periodo_atual_fim`, `periodo_anterior_inicio`, `periodo_anterior_fim`
  - `aberturas_atual`, `aberturas_anterior`, `aberturas_variacao_abs`, `aberturas_variacao_pct` (`numeric(8,2)`)
  - `ainda_ativos_atual`, `ainda_ativos_anterior`
  - `sobrevivencia_atual_pct` / `sobrevivencia_anterior_pct` (`numeric(5,2)`), `sobrevivencia_delta_pp` (`numeric(6,2)`)
  - `baixas_atual`, `baixas_anterior`, `baixas_variacao_abs`, `baixas_variacao_pct` (`numeric(8,2)`)
  - `saldo_atual`, `saldo_anterior`
- **Índices:**
  - `idx_mv_comp_territorio_pk` UNIQUE (`nivel`, `cod_regiao_ibge`, `cod_estado_ibge`, `cod_cidade_ibge`, `periodo_meses`)
  - `idx_mv_comp_territorio_estado` (`cod_estado_ibge`, `periodo_meses`)
  - `idx_mv_comp_territorio_cidade` (`cod_cidade_ibge`, `periodo_meses`)
  - `idx_mv_comp_territorio_regiao` (`cod_regiao_ibge`, `periodo_meses`)
- **Níveis:** `brasil` | `regiao` | `estado` | `municipio` (ver
  [Sentinela 0](#sentinela-0-nas-mvs-de-comparativo)).
- **Nota:** linhas de `mv_abertura_periodo` com `cod_cidade_ibge` NULL entram nos
  níveis brasil/região/estado, mas não geram linha de município — a soma dos
  municípios de uma UF pode ficar ligeiramente abaixo do total da própria UF.

#### `mv_comparativo_cnae`
- **Colunas:** `cod_cnae_principal` + as mesmas de `mv_comparativo_territorio`.
- **Índices:**
  - `idx_mv_comp_cnae_pk` UNIQUE (`cod_cnae_principal`, `nivel`, `cod_regiao_ibge`, `cod_estado_ibge`, `periodo_meses`)
  - `idx_mv_comp_cnae_estado` (`cod_estado_ibge`, `periodo_meses`)
  - `idx_mv_comp_cnae_regiao` (`cod_regiao_ibge`, `periodo_meses`)
  - `idx_mv_comp_cnae_variacao` (`nivel`, `periodo_meses`, `aberturas_variacao_pct` DESC)
- **Níveis:** `brasil` | `regiao` | `estado`. `cod_cidade_ibge` é sempre 0.

#### `mv_comparativo_natureza`
- **Colunas:** `cod_natureza` + as mesmas de `mv_comparativo_territorio`.
- **Índices:**
  - `idx_mv_comp_natureza_pk` UNIQUE (`cod_natureza`, `nivel`, `cod_regiao_ibge`, `cod_estado_ibge`, `periodo_meses`)
  - `idx_mv_comp_natureza_estado` (`cod_estado_ibge`, `periodo_meses`)
  - `idx_mv_comp_natureza_regiao` (`cod_regiao_ibge`, `periodo_meses`)
- **Níveis:** `brasil` | `regiao` | `estado`. `cod_cidade_ibge` é sempre 0.

### Função de Refresh

`refresh_all_mvs()` (definida em `sql/materialized_views/99_refresh_function.sql`)
devolve **uma linha por MV** — `OK`, `ERRO: <mensagem>` ou `AUSENTE`:

```sql
SELECT * FROM refresh_all_mvs();
```

- Cada MV tem seu próprio `BEGIN ... EXCEPTION`: uma falha isolada não
  interrompe as demais (na versão anterior, a primeira falha abortava tudo e a
  função devolvia uma única linha `ERROR`, sem dizer o que tinha dado certo).
- `CONCURRENTLY` é decidido em tempo de execução consultando `pg_indexes` — a MV
  precisa de índice único. Antes a escolha era escrita à mão e ficava
  desatualizada a cada índice novo.
- Ordem: estoques → séries mensais → comparativos, porque os comparativos leem
  as séries.

### Queries de sanidade pós-build

```sql
-- 1. Âncora única em todas as MVs (deve retornar 1)
SELECT count(DISTINCT mes_ancora) FROM (
    SELECT mes_ancora FROM mv_stats_estado
    UNION SELECT mes_ancora FROM mv_abertura_periodo
    UNION SELECT mes_ancora FROM mv_movimentacao_mensal_cnae
    UNION SELECT mes_ancora FROM mv_comparativo_territorio
) t;

-- 2. Comparativo de 12 meses == novos_1ano das stats (deve retornar 0)
SELECT count(*) FROM mv_comparativo_territorio c
  JOIN mv_stats_estado s ON s.cod_estado_ibge = c.cod_estado_ibge
 WHERE c.nivel = 'estado' AND c.periodo_meses = 12
   AND c.aberturas_atual <> s.novos_1ano;

-- 3. Brasil == soma das regiões == soma dos estados (deve retornar 0)
SELECT count(*) FROM (
    SELECT periodo_meses,
           sum(aberturas_atual) FILTER (WHERE nivel = 'brasil') AS br,
           sum(aberturas_atual) FILTER (WHERE nivel = 'regiao') AS reg,
           sum(aberturas_atual) FILTER (WHERE nivel = 'estado') AS est
      FROM mv_comparativo_territorio GROUP BY periodo_meses
) t WHERE br <> reg OR br <> est;

-- 4. Soma das baixas da série == count de '08' desde 2000 (deve retornar 0)
SELECT (SELECT sum(baixas) FROM mv_abertura_periodo)
     - (SELECT count(*) FROM estabelecimento
         WHERE cod_situacao_cadastral = '08'
           AND data_situacao_cadastral >= DATE '2000-01-01');

-- 5. anterior = 0 => variação NULL (deve retornar 0)
SELECT count(*) FROM mv_comparativo_territorio
 WHERE aberturas_anterior = 0 AND aberturas_variacao_pct IS NOT NULL;

-- 6. Toda MV aceita REFRESH CONCURRENTLY (deve retornar 0 linhas)
SELECT m.matviewname FROM pg_matviews m
 WHERE m.schemaname = 'public'
   AND NOT EXISTS (SELECT 1 FROM pg_indexes i
                    WHERE i.schemaname = 'public' AND i.tablename = m.matviewname
                      AND i.indexdef LIKE 'CREATE UNIQUE INDEX%');

-- 7. refresh_all_mvs() devolve 19 linhas, nenhuma com ERRO
SELECT status, count(*) FROM refresh_all_mvs() GROUP BY status;
```

---

## Consultas Básicas

### Buscar empresa por CNPJ completo

```sql
SELECT 
    est.cnpj_completo,
    e.razao_social,
    est.nome_fantasia,
    est.uf,
    cid.nome_cidade
FROM estabelecimento est
JOIN empresa e ON est.cnpj_basico = e.cnpj_basico
LEFT JOIN ibge_cidade cid ON est.cod_cidade_ibge = cid.cod_cidade_ibge
WHERE est.cnpj_completo = '12345678000100';
```

### Buscar empresa por CNPJ parcial (8 dígitos)

```sql
SELECT 
    est.cnpj_completo,
    e.razao_social,
    CASE est.matriz_filial 
        WHEN '1' THEN 'MATRIZ' 
        WHEN '2' THEN 'FILIAL' 
    END AS tipo
FROM estabelecimento est
JOIN empresa e ON est.cnpj_basico = e.cnpj_basico
WHERE est.cnpj_basico = '12345678';
```

### Listar sócios de uma empresa

```sql
SELECT 
    s.nome_socio,
    q.nome_qualificacao AS qualificacao,
    s.data_entrada_sociedade,
    CASE s.identificador_socio
        WHEN '1' THEN 'Pessoa Jurídica'
        WHEN '2' THEN 'Pessoa Física'
        WHEN '3' THEN 'Estrangeiro'
    END AS tipo_socio
FROM socio s
JOIN qualificacao_socio q ON s.cod_qualificacao_socio = q.cod_qualificacao
WHERE s.cnpj_basico = '12345678'
ORDER BY s.data_entrada_sociedade;
```

---

## Consultas com Localização (IBGE)

### Estabelecimentos por estado

```sql
SELECT 
    uf.sigla_uf,
    uf.nome_estado,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE est.cod_situacao_cadastral = '02') AS ativos
FROM estabelecimento est
JOIN ibge_estado uf ON est.cod_estado_ibge = uf.cod_estado_ibge
GROUP BY uf.sigla_uf, uf.nome_estado
ORDER BY total_estabelecimentos DESC;
```

### Estabelecimentos por cidade com coordenadas

```sql
SELECT 
    cid.nome_cidade,
    uf.sigla_uf,
    cid.latitude,
    cid.longitude,
    COUNT(*) AS total
FROM estabelecimento est
JOIN ibge_cidade cid ON est.cod_cidade_ibge = cid.cod_cidade_ibge
JOIN ibge_estado uf ON cid.cod_estado_ibge = uf.cod_estado_ibge
WHERE est.cod_situacao_cadastral = '02'
GROUP BY cid.nome_cidade, uf.sigla_uf, cid.latitude, cid.longitude
ORDER BY total DESC
LIMIT 20;
```

### Empresas por região

```sql
SELECT 
    r.nome_regiao,
    COUNT(DISTINCT est.cnpj_basico) AS total_empresas,
    COUNT(*) AS total_estabelecimentos
FROM estabelecimento est
JOIN ibge_regiao r ON est.cod_regiao_ibge = r.cod_regiao_ibge
WHERE est.cod_situacao_cadastral = '02'
GROUP BY r.nome_regiao
ORDER BY total_empresas DESC;
```

---

## Consultas com CNAEs

### CNAEs secundários de um estabelecimento

```sql
SELECT 
    sec.cnpj_completo,
    sec.cod_cnae,
    cn.nome_cnae
FROM estabelecimento_cnae_sec sec
JOIN cnae cn ON sec.cod_cnae = cn.cod_cnae
WHERE sec.cnpj_completo = '12345678000100';
```

### Empresas por CNAE em um estado

```sql
SELECT 
    cn.cod_cnae,
    cn.nome_cnae,
    COUNT(*) AS total
FROM estabelecimento est
JOIN cnae cn ON est.cod_cnae_principal = cn.cod_cnae
WHERE est.cod_estado_ibge = 35
  AND est.cod_situacao_cadastral = '02'
GROUP BY cn.cod_cnae, cn.nome_cnae
ORDER BY total DESC
LIMIT 20;
```

---

## Consultas com Materialized Views

### Estatísticas por estado

```sql
SELECT 
    sigla_uf,
    nome_estado,
    total_estabelecimentos,
    ativos,
    matrizes_ativas,
    total_empresas,
    novos_1ano
FROM mv_stats_estado
ORDER BY total_estabelecimentos DESC;
```

### Top 10 municípios por total de empresas

```sql
SELECT 
    nome_cidade,
    sigla_uf,
    total_empresas,
    ativos,
    novos_6meses
FROM mv_stats_municipio
ORDER BY total_empresas DESC
LIMIT 10;
```

### CNAEs mais comuns no país

```sql
SELECT 
    cod_cnae_principal,
    nome_cnae,
    total_estabelecimentos,
    ativos,
    estados_presentes
FROM mv_stats_cnae
ORDER BY total_estabelecimentos DESC
LIMIT 20;
```

### Evolução de aberturas por mês em SP

```sql
-- A MV tem grão municipal; recortes estaduais somam as cidades.
-- empresas_unicas somado é aproximado (ver nota da MV).
SELECT
    mes_abertura,
    SUM(total_aberturas)  AS total_aberturas,
    SUM(empresas_unicas)  AS empresas_unicas,
    SUM(ainda_ativos)     AS ainda_ativos
FROM mv_abertura_periodo
WHERE cod_estado_ibge = 35
  AND mes_abertura >= '2023-01-01'
GROUP BY mes_abertura
ORDER BY mes_abertura;
```

### Evolução de aberturas por mês em um município

```sql
SELECT
    mes_abertura,
    total_aberturas,
    empresas_unicas,
    ainda_ativos
FROM mv_abertura_periodo
WHERE cod_cidade_ibge = 3550308   -- São Paulo/SP
  AND mes_abertura >= '2023-01-01'
ORDER BY mes_abertura;
```

### Top CNAEs por cidade específica

```sql
SELECT 
    cod_cnae_principal,
    nome_cnae,
    total,
    ranking
FROM mv_top_cnaes_cidade
WHERE cod_cidade_ibge = 3550308
ORDER BY ranking;
```
