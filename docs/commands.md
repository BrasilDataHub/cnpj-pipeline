# Referência de Comandos

## Visão Geral

```bash
# Execução local
python etl.py <comando> [opções]

# Execução via Docker (primeiro plano — prende o terminal)
docker compose run --rm etl <comando> [opções]

# Execução via Docker em segundo plano (comandos longos, como `complete`)
docker compose run -d --name cnpj-run etl <comando> [opções]
tail -f data/logs/etl-$(date -u +%F).log   # container roda em UTC
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
| `db logged` | Converte tabelas UNLOGGED para LOGGED (durabilidade) |
| `db pk` | Adiciona chaves primárias |
| `db index` | Cria todos os índices (básicos + avançados) |
| `db fk` | Cria chaves estrangeiras |
| `db search` | Constrói/reconstrói a tabela de busca `busca_estabelecimento` (build-and-swap) |
| `db dead-letter` | Lista/reprocessa lotes de COPY preservados após falha na carga |
| `db views create` | Cria/recria Materialized Views |
| `db views refresh` | Atualiza dados das Materialized Views |
| `complete` | Executa todo o pipeline (download + carga + views) |

---

## Opções Globais

Estas opções funcionam com **todos** os comandos.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--log-file` | `path` | `data/logs/etl-YYYY-MM-DD.log` | Arquivo de log (append) com rotação diária |

**Rotação simples por data**
- Se o caminho for um diretório (ou terminar com `/`), o arquivo será `etl-YYYY-MM-DD.log`.
- Se o caminho for um arquivo, a data será inserida antes da extensão.
- Use `{date}` para controlar o formato: `--log-file data/logs/etl-{date}.log`.

**Via variável de ambiente**
- Defina `LOG_FILE` para um caminho de arquivo ou diretório.
- `--log-file` sempre tem prioridade sobre `LOG_FILE`.

---

## Opções de Observabilidade

Disponíveis em `download`, `complete` e nos subcomandos `db` — **exceto
`db views refresh`**, que é manutenção recorrente, não abre estado nem registra
execução (passar estas flags a ele resulta em erro de argumento).
Guia completo: [Observabilidade e retomada](observabilidade.md).

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--force` / `--force-restart` | `flag` | desligado | Ignora o estado do período e reexecuta tudo; o estado anterior vira `.bak-<timestamp>` |
| `--no-state` | `flag` | desligado | Desliga o checkpoint/retomada (não lê nem grava o arquivo de estado) |
| `--reference-period` | `str` | inferido | Período dos dados (`AAAA-MM` ou `MM/AAAA`), para subcomandos sem `--month` |
| `--max-attempts` | `int` | `3` | Tentativas por etapa antes de exigir intervenção (`0` = ilimitado) |
| `--serve` | `flag` | desligado | Sobe o dashboard web somente leitura durante a execução |
| `--port` | `int` | `3010` | Porta do dashboard |
| `--host` | `str` | `127.0.0.1` | Interface do dashboard (use `0.0.0.0` em container) |
| `--dashboard-password` | `str` | gerada | Senha do dashboard (Basic Auth); se omitida, é gerada e mostrada no log |
| `--dashboard-user` | `str` | `pipeline` | Usuário do dashboard |
| `--no-auth` | `flag` | desligado | Serve o dashboard sem autenticação (rede confiável) |
| `--webhook-url` | `url` | — | Notificações HTTP por etapa |

**Retomada é o comportamento padrão.** Reexecutar o mesmo comando após uma
interrupção pula as etapas já concluídas:

```bash
# Interrompeu na etapa de views? Basta repetir — o resto é pulado.
python etl.py complete --month 07/2026

# Acompanhar pelo navegador e notificar um endpoint a cada etapa
python etl.py complete --month 07/2026 \
    --serve --port 3010 \
    --webhook-url https://exemplo.com/hook
```

**Variáveis equivalentes:** `PIPELINE_WEBHOOK_URL`, `PIPELINE_PORT`,
`PIPELINE_MAX_ATTEMPTS`, `PIPELINE_STATE_DIR`, `PIPELINE_DASHBOARD_PASSWORD`,
`PIPELINE_DASHBOARD_USER`, `PIPELINE_REFRESH_SECONDS`. As flags têm prioridade.

---

## Comandos de Consulta

```bash
# Lista todos os meses disponíveis
python etl.py get-availables

# Retorna o mês mais recente
python etl.py get-latest

# Exibe URLs de download para um mês
python etl.py get-urls --month 07/2026
```

---

## Comando `download`

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
python etl.py download --month 07/2026

# Baixar com limpeza prévia e 4 workers
python etl.py download --month 07/2026 --clean --workers 4
```

---

## Comando `db init`

Cria o schema e as tabelas no banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db init
```

---

## Comando `db load`

Carrega os dados dos arquivos ZIP para o banco de dados.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--month` | `MM/AAAA` | Último mês | Mês a ser carregado |
| `--download-dir` | `path` | `data/downloads/YYYY-MM` | Pasta com os arquivos ZIP |
| `--skip-index` | flag | - | Não cria índices ao final |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection frequente |
| `--parallel` | `true\|false` | `true` | Multi-threading na carga (`--parallel false` desliga) |
| `--only-data` | flag | - | Carrega apenas dados (sem patch/logged/pk/index/fk/search) |

```bash
# Carga completa padrão (inclui todos os índices)
python etl.py db load --month 07/2026

# Carga apenas dados (sem extras)
python etl.py db load --month 07/2026 --only-data

# Carga sem paralelismo (o padrão já é paralelo)
python etl.py db load --month 07/2026 --parallel false
```

**Lote defeituoso não derruba a carga.** Um lote de COPY que falha é retentado
(com reconexão); se falhar definitivamente — o que costuma ser dado malformado
vindo do próprio arquivo da RFB — as linhas são **isoladas** em
`data/logs/dead_letter/` (CSV + `.meta` com tabela/colunas/arquivo de origem),
a contagem de inseridos **não** inclui o lote perdido, e a perda fica
**documentada** no log (aviso destacado), no metadata da etapa `data_load` no
JSON de estado e no dashboard (nota âmbar na etapa). O pipeline **segue
normalmente** — um lote fora do nosso controle não pode condenar uma carga de
horas. Para fechar o ciclo, use [`db dead-letter`](#comando-db-dead-letter).

---

## Comandos `db patch`, `db logged`, `db pk`, `db index`, `db fk`

Executam etapas específicas do processo de carga.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db patch    # Aplica correções estáticas
python etl.py db logged   # Converte tabelas UNLOGGED para LOGGED (durabilidade)
python etl.py db pk       # Adiciona chaves primárias
python etl.py db index    # Cria todos os índices (básicos + avançados)
python etl.py db fk       # Cria chaves estrangeiras
```

O comando `db patch` executa, em ordem: inserção de dados de referência
faltantes (países, qualificações, motivos), normalizações (`LPAD` de
`cod_pais`, `cod_porte` vazio → `'00'`), a **absorção automática de códigos de
país órfãos** (códigos presentes em `estabelecimento`/`socio` mas ausentes do
arquivo PAISCSV do mês entram na tabela `pais` como `CODIGO NAO CONSTANTE NA
TABELA RFB` — sem isso as FKs de país falhariam, o modo de falha do incidente
de 07/2026), deduplicação de `empresa` e `VACUUM ANALYZE`.

O comando `db logged` converte as tabelas de UNLOGGED para LOGGED em ordem
topológica de FK (menor→maior dentro de cada nível). Sem essa etapa, um crash
da instância **truncaria** as tabelas. Custo: reescrita completa com WAL
(+1–3 h nas 5 tabelas grandes).

O comando `db index` cria automaticamente:
- **Índices básicos**: BTREE simples para JOINs, FKs e consultas comuns (19 índices)
- **Índices avançados** (40 índices):
  - **GIN (pg_trgm)**: Busca textual com `LIKE '%termo%'` em nome fantasia, razão social, bairro e nome de sócios
  - **BRIN**: Índices compactos para colunas de data (economia de ~95% de espaço)
  - **HASH**: Lookups ultra-rápidos para email
  - **Parciais**: Índices apenas para empresas ativas ou com email preenchido
  - **Compostos**: Otimizados para consultas de prospecção, filtros por localização e CNAE

**Performance**: os índices são criados **sem** `CONCURRENTLY` (o banco de
carga não tem leitores) e em paralelo por índice — `INDEX_MAX_WORKERS`
conexões, cada uma com `maintenance_work_mem = INDEX_MAINTENANCE_WORK_MEM`
(ver [Configuração](configuration.md#criação-de-índices)).

**Falhas não passam em silêncio**: tanto `db index` quanto `db fk` tentam criar
todos os objetos e, se qualquer um falhar, a etapa termina como **FALHA**
listando o que ficou faltando (antes, erros de FK eram apenas logados e a
etapa "concluía" — foi assim que duas FKs sumiram sem alarde em 07/2026).

---

## Comando `db search`

Constrói (ou reconstrói) a **tabela de busca enxuta** `busca_estabelecimento`:
uma linha por estabelecimento, apenas os campos filtráveis da busca do website
e nomes normalizados com `unaccent(upper(...))` (razão social, nome fantasia
e bairro). É a etapa 6.5 do pipeline de carga — roda automaticamente no
`db load`/`complete` — e pode ser executada isoladamente após qualquer carga.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |

```bash
python etl.py db search
```

**Build-and-swap (zero downtime de leitura):**
1. `CREATE UNLOGGED TABLE busca_estabelecimento_new AS SELECT ...` (CTAS rápido, sem WAL);
2. validação de contagem (1 linha por linha de `estabelecimento` — o build aborta se divergir, mantendo a tabela vigente);
3. `ALTER TABLE ... SET LOGGED` (durabilidade antes de indexar);
4. PK em `cnpj_completo` + índices com sufixo `_new` (2 GIN trigram nas colunas de nome normalizadas + 2 btrees compostos) + `ANALYZE`;
5. **uma única transação**: `DROP` da tabela vigente + `RENAME` da nova + renomeação de PK/índices — leitores nunca veem estado intermediário.

O comando é idempotente: restos de builds interrompidos (`*_new`) são
descartados no início, e a troca funciona tanto na primeira execução
(sem tabela vigente) quanto nas recriações mensais.

**Requisitos:** extensão `unaccent` (criada no `db init`) e tabelas
`estabelecimento`/`empresa` carregadas. Tamanho estimado em produção:
10–12 GB para 72M de linhas.

---

## Comandos do ciclo mensal blue/green

`db bootstrap`, `db cycle`, `db validate`, `db publish`, `db rollback`, `db gc`
e `db nuke` formam o ciclo mensal introduzido pelos itens 24–26 do roadmap 20.
Eles substituem o antigo primeiro passo do ETL — o `DROP` das tabelas — por uma
carga em schema novo, com o schema anterior servindo o site o tempo todo.

| Comando | O que faz | Publica? |
|---------|-----------|----------|
| `db bootstrap --load-id YYYYMM` | cria `ext`, `meta` e `dados_<load_id>`; revoga escrita no schema vigente | não |
| `db cycle --load-id YYYYMM` | `bootstrap` + instrução do próximo passo | não |
| `db validate --load-id YYYYMM` | portão de qualidade: contagem, MVs e gate de delta | **não** |
| `db publish --load-id YYYYMM` | valida e troca o `search_path` (< 5 s) | sim |
| `db rollback [--para SCHEMA]` | volta para a geração anterior (< 60 s) | sim |
| `db gc [--apagar]` | lista o N−2 em diante; sem `--apagar` não apaga nada | não |
| `db nuke --i-know-what-im-doing` | destrói o schema. Fora do ciclo mensal | não |

```bash
python etl.py db bootstrap --load-id 202608
python etl.py db load      --load-id 202608     # e as demais etapas de carga
python etl.py db validate  --load-id 202608     # reprova sem publicar
python etl.py db publish   --load-id 202608     # < 5 s
python etl.py db rollback                       # se preciso, < 60 s
```

Todos os verbos pegam o `flock` compartilhado em `PIPELINE_LOCK_FILE`, **exceto
`rollback` e `gc`** — um rollback precisa acontecer justamente quando alguma
coisa está travada.

> A explicação completa (os schemas, o gate de delta, o `REVOKE`, a
> sequência de um mês) está em **[ciclo-blue-green.md](ciclo-blue-green.md)**.

---

## Comando `db dead-letter`

Fecha o ciclo dos **lotes de COPY que falharam definitivamente** durante a
carga. Cada lote perdido fica preservado no diretório dead-letter
(`data/logs/dead_letter/`, configurável via `PIPELINE_DEAD_LETTER_DIR`) como
um par de arquivos:

- `<tabela>-<timestamp>.csv` — o payload exato que o COPY rejeitou
  (windows-1252, separado por `;`);
- `<tabela>-<timestamp>.meta` — tabela, colunas, arquivo de origem e número
  de linhas.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--retry` | flag | - | Tenta recarregar cada lote; os que carregarem vão para `processed/` |
| `--dir` | `path` | `data/logs/dead_letter` | Diretório dead-letter |

```bash
# Listar o que está preservado (tabela, linhas, arquivo de origem)
python etl.py db dead-letter

# Retentar a carga de todos os lotes (causas transitórias)
python etl.py db dead-letter --retry

# Intervenção manual: edite o CSV (ex.: corrija a linha malformada da RFB)
# e reprocesse — só os lotes corrigidos carregam; os demais permanecem
vim data/logs/dead_letter/cnae-20260726T120000.csv
python etl.py db dead-letter --retry
```

Comportamento:
- cada lote é **uma transação**: ou entra inteiro, ou nada entra;
- lote carregado com sucesso é movido para `processed/` (trilha de auditoria
  preservada, sem risco de recarga dupla);
- lote que ainda falha permanece no lugar, com o erro no log, e o comando
  termina com **exit code 1** — dá para automatizar o retry em cron e ser
  alertado só quando sobrar algo que exige intervenção manual.

Assim como `db views refresh`, é um comando de manutenção: não abre estado nem
registra execução em `pipeline_stats`.

---

## Comandos `db views create` e `db views refresh`

Comandos **opcionais** para criação e atualização de Materialized Views (MVs).

As MVs pré-computam estatísticas agregadas que reduzem consultas de minutos para milissegundos.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--only` | `string` | - | Recria só os SQLs cujo nome contenha um dos trechos (apenas para `create`) |
| `--concurrent` | flag | - | Usa `REFRESH CONCURRENTLY` (apenas para `refresh`) |

```bash
# Criar/recriar todas as Materialized Views
python etl.py db views create

# Recriar apenas algumas (base já carregada, sem recarregar nada)
python etl.py db views create --only 00,05,14

# Atualizar dados das MVs (após nova carga)
python etl.py db views refresh

# Atualizar sem bloquear leituras (requer índice único nas MVs)
python etl.py db views refresh --concurrent
```

#### `--only`: recriação seletiva

O valor é uma lista separada por vírgula de **trechos do nome do arquivo**
(`00,05,14` casa `00_helpers.sql`, `05_mv_abertura_periodo.sql` e
`14_mv_movimentacao_mensal_cnae.sql`). Um trecho que não casa com nenhum
arquivo interrompe o comando antes de tocar no banco, em vez de recriar menos
MVs do que o pedido.

**A seleção é expandida automaticamente com as MVs dependentes.** As MVs de
comparativo leem as séries mensais, então o `DROP MATERIALIZED VIEW ... CASCADE`
do topo do arquivo da série derruba junto a MV de comparativo. Sem a expansão,
um `--only 05` deixaria o banco **sem** `mv_comparativo_territorio` e reportaria
sucesso. As dependências são registradas em `MV_FILE_DEPENDENTS`
(`db/postgres_builder.py`):

| Selecionando | Entra junto |
|--------------|-------------|
| `05_mv_abertura_periodo` | `17_mv_comparativo_territorio` |
| `14_mv_movimentacao_mensal_cnae` | `18_mv_comparativo_cnae` |
| `15_mv_movimentacao_mensal_natureza` | `19_mv_comparativo_natureza` |

A expansão aparece no log como aviso, com o motivo.

Cada arquivo derruba e recria a sua MV, então **a indisponibilidade é por MV**,
não do banco inteiro: durante a recriação de `mv_abertura_periodo` (~15 min) as
demais MVs continuam respondendo.

#### Ordem do `refresh`

O `refresh` descobre as MVs existentes no catálogo e as ordena por
**dependência** (`pg_depend`/`pg_rewrite`), não por nome. Alfabeticamente
`mv_comparativo_*` vem antes de `mv_movimentacao_*`, mas lê dessa MV — a ordem
alfabética publicaria um comparativo montado sobre a série do mês anterior.
Se a consulta de dependências falhar, o comando cai para a ordem alfabética e
avisa no log: uma ordem ruim ainda atualiza tudo.

Sobre o registro em `pipeline_stats`:
- `db views create` (sem `--only`) participa do estado e pode fechar a execução
  do período como `completed` quando for a última etapa obrigatória pendente;
- `db views create --only` e `db views refresh` são **manutenção**: não abrem
  estado nem criam linha própria, mas **carimbam `views_refreshed_at`** na
  execução mais recente do período — é o sinal que consumidores (ex.: o site)
  usam para saber que as MVs mudaram. Abrir estado num `--only` deixaria uma
  execução pela metade no dashboard e faria uma retomada posterior acreditar
  que a etapa de views já tinha rodado inteira naquele mês;
- subcomandos isolados que terminam sem completar todas as etapas obrigatórias
  gravam `status = 'partial'`, nunca `completed` (ver
  [Observabilidade](observabilidade.md)).

### Materialized Views disponíveis

| View | Descrição | Tempo estimado |
|------|-----------|----------------|
| `mv_stats_estado` | Estatísticas agregadas por estado | ~2 min |
| `mv_stats_municipio` | Estatísticas agregadas por município | ~5 min |
| `mv_stats_cnae` | Estatísticas agregadas por CNAE | ~3 min |
| `mv_stats_cnae_estado` | Estatísticas detalhadas CNAE x Estado | ~10 min |
| `mv_abertura_periodo` | Movimentação mensal por município (desde 2000) | ~15 min |
| `mv_top_cnaes_cidade` | Top 20 CNAEs por cidade | ~15 min |
| `mv_stats_cidade_situacao` | Estatísticas por cidade x situação cadastral | ~8 min |
| `mv_regime_tributario_cidade` | Regime tributário (Simples/MEI) por cidade | ~8 min |
| `mv_porte_cidade` | Porte de empresa por cidade | ~6 min |
| `mv_stats_natureza_juridica_estado` | Estatísticas por natureza jurídica x estado | ~6 min |
| `mv_stats_natureza_juridica_municipio` | Estatísticas por natureza jurídica x município | ~10 min |
| `mv_stats_natureza_juridica` | Estatísticas agregadas por natureza jurídica | ~3 min |
| `mv_stats_natureza_juridica_cnae` | Estatísticas por natureza jurídica x CNAE | ~8 min |
| `mv_movimentacao_mensal_cnae` | Série mensal por CNAE x UF | ~15 min |
| `mv_movimentacao_mensal_natureza` | Série mensal por natureza jurídica x UF | ~20 min |
| `mv_movimentacao_mensal_porte` | Série mensal por porte x UF | ~20 min |
| `mv_comparativo_territorio` | Comparativo entre períodos por território | ~10 s |
| `mv_comparativo_cnae` | Comparativo entre períodos por CNAE | ~1 min |
| `mv_comparativo_natureza` | Comparativo entre períodos por natureza jurídica | ~10 s |

As três MVs de comparativo são montadas a partir das séries mensais (não da
tabela `estabelecimento`), por isso o tempo de build é desprezível.

**Arquivos SQL:** Os scripts estão em `sql/materialized_views/` e são executados
na ordem alfabética. O `00_helpers.sql` roda antes de tudo e define
`fn_mes_ancora()`, a âncora temporal de todas as janelas — ver
[Âncora temporal e semântica de períodos](database.md#âncora-temporal-e-semântica-de-períodos).

**Periodicidade de refresh recomendada:**
- `mv_stats_estado`, `mv_stats_cnae`: Diário
- Demais MVs: Semanal ou quinzenal

**Espaço estimado:** ~5 GB

---

## Comando `complete`

Executa o pipeline completo: **download + carga + Materialized Views** em sequência.

| Flag | Tipo | Padrão | Descrição |
|------|------|--------|-----------|
| `--month` | `MM/AAAA` | Último mês | Mês de referência |
| `--db-name` | `string` | `dados_cnpj` | Nome do banco |
| `--download-dir` | `path` | `data/downloads` | Diretório de download (os ZIPs vão para `<dir>/AAAA-MM/`, e a carga lê da mesma subpasta) |
| `--workers` | `int` | `10` | Downloads simultâneos |
| `--clean` | flag | - | Remove arquivos antes de baixar |
| `--skip-index` | flag | - | Não cria índices |
| `--skip-validation` | flag | - | Ignora verificação dos arquivos |
| `--low-memory` | flag | - | Ativa garbage collection |
| `--parallel` | `true\|false` | `true` | Multi-threading na carga (`--parallel false` desliga) |
| `--skip-download` | flag | - | Não baixa os arquivos, executa apenas as etapas do banco (**implica `--skip-validation`**) |
| `--skip-views` | flag | - | Não cria Materialized Views ao final |

> **Nota sobre `--download-dir`**: no `complete`, o valor é a **raiz** de
> downloads (o pipeline cria/lê a subpasta `AAAA-MM` dentro dele). Já no
> `db load`, `--download-dir` aponta **diretamente** para a pasta que contém
> os ZIPs.

```bash
# Pipeline completo (inclui índices e Materialized Views)
python etl.py complete --month 07/2026 --parallel --clean

# Apenas etapas do banco (arquivos já baixados)
python etl.py complete --month 07/2026 --parallel --skip-download
```

> Este comando leva **horas**. Em servidor, rode em segundo plano em vez de
> deixá-lo preso ao terminal — via Docker com `docker compose run -d --name ...`
> ([Guia Docker](docker.md#execução-em-segundo-plano-detached)) ou, em execução
> local com Python, com `nohup python etl.py complete ... &`. Nos dois casos, o
> acompanhamento é o mesmo: `tail -f data/logs/etl-$(date -u +%F).log`
> (via Docker o container roda em UTC; em execução local, use `date +%F`).

---

## Execução por Etapas

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
| 5 | `python etl.py db logged` | Converte tabelas UNLOGGED para LOGGED |
| 6 | `python etl.py db pk` | Adiciona chaves primárias |
| 7 | `python etl.py db index` | Cria todos os índices (básicos + avançados) |
| 8 | `python etl.py db fk` | Cria chaves estrangeiras |
| 9 | `python etl.py db search` | Constrói a tabela de busca `busca_estabelecimento` |
| 10 | `python etl.py db views create` | *(Opcional)* Cria Materialized Views |

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
python etl.py download --month 07/2026
python etl.py db load --only-data --month 07/2026
python etl.py db patch
python etl.py db logged
python etl.py db pk
python etl.py db index
python etl.py db fk
python etl.py db search
```

---

## Códigos de Saída

O CLI termina com código **≠ 0 em qualquer falha** — inclusive as de validação
(mês inválido, pasta de arquivos ausente, validação de ZIP reprovada), que são
logadas em português e encerram com código `1`. Isso permite que cron/CI
detectem falhas de forma confiável:

```bash
python etl.py complete --month 07/2026 || notificar-falha.sh
```

---

## Ajuda

```bash
python etl.py --help
python etl.py download --help
python etl.py db --help
python etl.py db load --help
python etl.py db views --help
python etl.py db views create --help
python etl.py db views refresh --help
```
