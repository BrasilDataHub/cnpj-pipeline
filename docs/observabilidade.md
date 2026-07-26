# Observabilidade, estado e retomada

Quatro capacidades que acompanham uma execução do pipeline e permitem retomá-la
de onde parou:

| Capacidade | Como se ativa | Onde vive |
|---|---|---|
| [Estado e retomada](#estado-e-retomada) | **padrão** (desligue com `--no-state`) | `data/state/pipeline_state_AAAA-MM.json` |
| [Tabela de estatísticas](#tabela-de-estatísticas) | automática quando há banco | tabela `pipeline_stats` |
| [Dashboard web](#dashboard-web) | `--serve` | `http://localhost:3010` |
| [Webhooks](#webhooks) | `--webhook-url` ou `PIPELINE_WEBHOOK_URL` | HTTP POST |

> **Por que isso existe.** Em 25/07/2026 uma execução rodou 6h43 e falhou na
> última etapa (materialized views). Todo o trabalho — download, carga, 40
> índices, tabela de busca — estava no banco, mas nada registrava isso: um
> `complete` ingênuo teria derrubado as tabelas e recomeçado do zero. Com
> estado, as etapas concluídas são puladas e a retomada custa minutos.

---

## Estado e retomada

### A janela é do dado, não do relógio

O estado é indexado pelo **período de referência dos arquivos da RFB**, não
pela data em que o pipeline rodou. Um pipeline que baixou os arquivos de
07/2026 no dia 25 e foi retomado no dia 26 continua no mesmo
`pipeline_state_2026-07.json` — a virada do dia civil não cria estado novo.

```
data/state/
└── pipeline_state_2026-07.json      ← período dos dados, não a data de hoje
```

O período é resolvido nesta ordem:

1. `--reference-period AAAA-MM` (declaração explícita);
2. `--month MM/AAAA` do próprio comando;
3. em `complete`/`download` sem `--month`, o mês mais recente da RFB;
4. o estado modificado mais recentemente em `data/state/` — é o que faz
   `db fk` ou `db views create` avulsos continuarem a execução em andamento.

Se nada resolver o período, o rastreamento é desligado com um aviso e o
pipeline roda normalmente.

### Comportamento de retomada

Ao iniciar, o pipeline lê o estado do período e, para cada etapa:

- `success` → **pula** (`ETAPA JÁ CONCLUÍDA, PULANDO: indices`);
- `pending` ou `failed` → **executa** (incrementando `attempts`);
- `failed` com `attempts >= --max-attempts` (padrão 3) → **aborta** com
  instrução, em vez de repetir o mesmo erro para sempre. É a proteção contra um
  cron que reexecuta sozinho.

Um estado ilegível ou corrompido não trava nada: é registrado um aviso e um
estado novo é criado. A gravação é atômica (arquivo temporário + `os.replace`),
então uma queda no meio da escrita não deixa JSON pela metade.

### Etapas rastreadas

Na ordem de execução:

| # | Nome no estado | Corresponde a |
|---|---|---|
| 1 | `download_arquivos` | download dos ZIPs da RFB |
| 2 | `validacao_arquivos` | validação dos ZIPs baixados |
| 3 | `schema_init` | criação do banco, extensões, tabelas e IBGE |
| 4 | `carga_dados` | `COPY` dos CSVs |
| 5 | `patches` | correções estáticas pós-carga |
| 6 | `tabelas_logged` | conversão UNLOGGED → LOGGED |
| 7 | `chaves_primarias` | primary keys |
| 8 | `indices` | índices básicos e avançados |
| 9 | `chaves_estrangeiras` | foreign keys |
| 10 | `tabela_busca` | `busca_estabelecimento` (build-and-swap) |
| 11 | `materialized_views` | as 13 MVs |

`db views refresh` **não** entra no estado: reexecutá-lo é sempre válido e não
há o que retomar.

### Schema do arquivo de estado

```json
{
  "run_id": "3f2a1b8c-5d6e-4f70-8a91-2b3c4d5e6f70",
  "reference_period": "2026-07",
  "created_at": "2026-07-25T14:32:10-03:00",
  "updated_at": "2026-07-25T15:10:44-03:00",
  "status": "in_progress",
  "steps": [
    {
      "name": "download_arquivos",
      "status": "success",
      "started_at": "2026-07-25T14:32:11-03:00",
      "finished_at": "2026-07-25T14:35:02-03:00",
      "error": null,
      "attempts": 1,
      "metadata": { "files_downloaded": 37, "total_bytes": 7648210944 }
    },
    {
      "name": "carga_dados",
      "status": "pending",
      "started_at": null,
      "finished_at": null,
      "error": null,
      "attempts": 0,
      "metadata": {}
    }
  ]
}
```

| Campo | Tipo | Descrição |
|---|---|---|
| `run_id` | UUID v4 | identifica a execução; preservado nas retomadas, novo a cada `--force` |
| `reference_period` | `AAAA-MM` | período dos dados da RFB |
| `created_at` / `updated_at` | ISO 8601 **com offset** | nunca sem fuso |
| `status` | `in_progress` \| `completed` \| `failed` | estado geral |
| `steps[].name` | string | nome da etapa (tabela acima) |
| `steps[].status` | `pending` \| `running` \| `success` \| `failed` | — |
| `steps[].started_at` / `finished_at` | ISO 8601 com offset \| `null` | — |
| `steps[].error` | string \| `null` | mensagem da exceção |
| `steps[].attempts` | inteiro | tentativas já feitas |
| `steps[].metadata` | objeto | métricas e progresso da etapa (ex.: `files_downloaded`, `records_inserted`, `tabela_atual`, `percentual`) |
| `environment` | objeto | onde a execução roda (ver [Ambiente e banco](#ambiente-e-banco)) |
| `database` | objeto | banco alvo; atualizado ao fim de cada etapa |

### `--force`

Ignora o estado do período e reexecuta tudo:

```bash
python etl.py complete --month 07/2026 --force
```

O estado anterior **não é descartado** — vira
`pipeline_state_2026-07.json.bak-20260725T225751` antes de ser substituído. Um
`run_id` novo é gerado, o que preserva a execução antiga como linha separada em
`pipeline_stats`.

> ⚠️ `--force` faz `complete` executar `schema_init` de novo, o que **derruba e
> recria as tabelas**. Para apenas refazer uma etapa específica, prefira o
> subcomando isolado (`python etl.py db views create`).

---

## Tabela de estatísticas

Uma **linha por execução**, chaveada por `run_id`, criada automaticamente
(`CREATE TABLE IF NOT EXISTS`) quando há banco acessível.

```sql
CREATE TABLE pipeline_stats (
    run_id                  UUID        PRIMARY KEY,
    reference_period        VARCHAR(7),
    status                  VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    started_at              TIMESTAMPTZ NOT NULL,
    finished_at             TIMESTAMPTZ,
    duration_seconds        NUMERIC,
    records_inserted_total  BIGINT,
    tables_populated        JSONB,
    files_downloaded_count  INTEGER,
    files_downloaded_detail JSONB,
    error                   TEXT
);
```

Instantes são `TIMESTAMPTZ` — data, hora e fuso num único campo, nunca em
colunas separadas.

**A tabela sobrevive à recarga.** `drop_tables()` varre `pg_tables` do schema
`public`; `pipeline_stats` está na lista de preservação
(`PRESERVED_TABLES`), senão o histórico seria apagado a cada `complete`.

`files_downloaded_detail` guarda um objeto por arquivo:

```json
[
  {
    "nome_arquivo": "Empresas0.zip",
    "tamanho_bytes": 48213120,
    "url_origem": "https://arquivos.receitafederal.gov.br/.../Empresas0.zip",
    "baixado_em": "2026-07-25T14:33:40-03:00"
  }
]
```

`tables_populated` usa a contagem viva do catálogo (`pg_stat_user_tables`), não
`COUNT(*)`: em `estabelecimento` (72M linhas) a contagem exata custaria minutos
só para preencher um metadado.

### Consultas úteis

```sql
-- Quanto durou a última execução?
SELECT reference_period, started_at, finished_at,
       (duration_seconds || 's')::interval AS duracao, status
  FROM pipeline_stats ORDER BY started_at DESC LIMIT 1;

-- Quantos arquivos foram baixados na execução de julho?
SELECT files_downloaded_count
  FROM pipeline_stats WHERE reference_period = '2026-07'
 ORDER BY started_at DESC LIMIT 1;

-- Evolução do volume mês a mês
SELECT reference_period, records_inserted_total,
       round(duration_seconds/3600, 2) AS horas
  FROM pipeline_stats WHERE status = 'completed'
 ORDER BY reference_period;

-- Arquivos maiores que 1 GB na última execução
SELECT d->>'nome_arquivo' AS arquivo,
       pg_size_pretty((d->>'tamanho_bytes')::bigint) AS tamanho
  FROM pipeline_stats, jsonb_array_elements(files_downloaded_detail) d
 WHERE run_id = (SELECT run_id FROM pipeline_stats ORDER BY started_at DESC LIMIT 1)
   AND (d->>'tamanho_bytes')::bigint > 1073741824;
```

---

## Dashboard web

Somente leitura. Lê o JSON de estado e nada mais — não tem lógica de negócio,
não escreve no estado e não toca no banco.

```bash
python etl.py complete --month 07/2026 --serve
python etl.py complete --month 07/2026 --serve --port 8090
```

| Rota | Conteúdo |
|---|---|
| `/` | página HTML |
| `/state.json` | o arquivo de estado, cru |

Ambas exigem autenticação — inclusive o JSON.

### Autenticação

O dashboard é protegido por **HTTP Basic Auth**: o navegador exibe o prompt
nativo, sem página de login para manter. Se nenhuma senha for informada, o
pipeline **gera uma** e a mostra no log — ele nunca sobe aberto por acidente.

```bash
# senha gerada automaticamente
python etl.py complete --month 07/2026 --serve
```
```
🌐 DASHBOARD DISPONÍVEL EM http://localhost:3010
🔍   -> usuário: pipeline   senha: k3Jq-8sPz2Vw   (exemplo)
ℹ️   -> senha gerada automaticamente; defina --dashboard-password para escolher a sua
```

```bash
# senha escolhida por você
python etl.py complete --month 07/2026 --serve --dashboard-password minha-senha

# via variável de ambiente (prático em container)
export PIPELINE_DASHBOARD_PASSWORD=minha-senha

# sem autenticação — só em rede confiável
python etl.py complete --month 07/2026 --serve --no-auth
```

O usuário padrão é `pipeline` (mude com `--dashboard-user`); o que importa é a
senha. A comparação usa `secrets.compare_digest` (tempo constante).

Com `curl`:

```bash
curl -u pipeline:minha-senha http://localhost:3010/state.json
```

> A senha gerada aparece no log — que também vai para
> `data/logs/etl-AAAA-MM-DD.log`. Para uma execução longa cujo log seja
> compartilhado, prefira `--dashboard-password`.

### A página

Atualiza sozinha a cada **6 s**, e o intervalo é ajustável na própria interface
(3s / 6s / 10s / 30s / pausar). A escolha fica no `localStorage` do navegador.

- **status geral** — situação, etapas concluídas/total, tempo decorrido, total
  de arquivos e de registros, com barra de progresso;
- **executando agora** — etapa em curso e há quanto tempo (cronômetro vivo);
- **timeline das etapas** — em colunas alinhadas (etapa · status · duração),
  com horário de início e fim, número de tentativas, metadados e a mensagem de
  erro de cada etapa que falhou;
- **progresso dentro da etapa em curso** — ver abaixo;
- **ambiente** — onde e como esta execução está rodando, e contra qual banco.

#### Cronômetro em tempo real

A duração de uma etapa **em execução** avança sozinha na tela, sem esperar a
etapa terminar: a página mantém um tick de 1 s e recalcula a partir de
`started_at`. O mesmo vale para o "decorrido" geral. Só o *conteúdo* (status,
progresso) depende do polling — o relógio não.

#### Progresso dentro da etapa

As duas etapas longas publicam o que está acontecendo por dentro, e o
dashboard mostra isso com uma barra fina abaixo do nome:

| Etapa | O que aparece |
|---|---|
| `download_arquivos` | `12 de 37 arquivos · 25 restantes · Empresas3.zip · 32.4%` |
| `carga_dados` | `98.721.430 de 218.380.000 registros · tabela estabelecimento · Estabelecimentos4.zip · 45.2%` |

Esses campos ficam em `steps[].metadata` (`tabela_atual`, `arquivo_atual`,
`records_inserted`, `records_total`, `arquivos_baixados`, `arquivos_total`,
`arquivos_restantes`, `percentual`) e só são exibidos enquanto a etapa está
`running`.

> A carga chama o publicador **a cada lote** — centenas de vezes. Para não
> transformar o estado em I/O no meio do `COPY`, a gravação é limitada a uma a
> cada 2 s (`PROGRESS_MIN_INTERVAL_SECONDS`); os valores em memória continuam
> sempre atualizados.

#### Ambiente e banco

Um card ao pé da página responde "onde isso está rodando?":

| Campo | Exemplo |
|---|---|
| execução | `Docker` ou `Python (direto)` |
| container / orquestrador | `a99f54270055` · `kubernetes` |
| máquina · IP | `v2202607386618488112` · `10.0.0.7` |
| sistema · arquitetura · CPUs · PID | `Linux 6.12.96` · `x86_64` · `12` · `1` |
| Python | `3.13.14` |
| banco · servidor · usuário | `dados_cnpj` · `localhost:15432` · `postgres` |
| versão · tamanho · conexões | `PostgreSQL 17.10` · `48 GB` · `9` |

A detecção de container usa `/.dockerenv` e `/proc/1/cgroup`; o IP é o da
interface que o SO usaria para sair da máquina (um `connect` UDP que **não
envia pacote**, só resolve a rota — `gethostbyname` costuma devolver
`127.0.0.1` dentro de container).

O bloco do banco é reavaliado **ao fim de cada etapa**, então dá para ver o
tamanho crescer ao longo da carga. **A senha nunca é coletada** — só host,
porta, database e usuário.

Acompanha tema claro e escuro conforme a preferência do sistema.

O título da aba acompanha o andamento (`10/11 · failed — CNPJ Pipeline`), o que
ajuda quando há várias abas abertas.

**Dependências:** o CSS é inline; a reatividade usa
[Alpine.js 3](https://alpinejs.dev) via CDN (jsDelivr). Quem baixa o script é o
**navegador de quem acessa**, não a máquina do ETL — o servidor só entrega o
HTML. Se o CDN não responder, a página exibe um aviso (em vez de ficar em
branco) e o `/state.json` continua servindo os dados. Para fixar uma versão
exata, troque `alpinejs@3` por `alpinejs@3.14.1` em `ALPINE_CDN`
(`utils/dashboard.py`).

### Não indexável

A página é um painel interno e efêmero, e sai de circulação para buscadores por
dois caminhos:

- `<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">`;
- header `X-Robots-Tag` com o mesmo conteúdo — necessário porque o
  `/state.json` não tem onde carregar uma `<meta>`.

Junto vão `X-Content-Type-Options: nosniff` e `Referrer-Policy: no-referrer`.

### Semântica e acessibilidade

Verificado com **axe-core** (0 violações, nos temas claro e escuro) e
**html-validate** (0 erros):

- landmarks `<main>`, `<header>`, `<footer>` e `<section>` rotuladas por
  `aria-labelledby`; um `<h1>` e `<h2>` por seção;
- métricas do resumo como lista de definições (`<dl>`/`<dt>`/`<dd>`) — são
  pares rótulo/valor, não parágrafos;
- barra de progresso com o elemento nativo **`<progress>`**, que já expõe
  valor e máximo às tecnologias assistivas;
- `aria-live="polite"` nas regiões que mudam sozinhas, para o leitor de tela
  anunciar a mudança sem interromper;
- `<label>` no seletor de intervalo (visualmente oculto por `.sr-only`);
- indicadores puramente decorativos marcados com `aria-hidden`;
- `prefers-reduced-motion` desliga o pulso e as transições (WCAG 2.3.3);
- `:focus-visible` com contorno de 2 px para navegação por teclado;
- `<noscript>` apontando para o `/state.json`;
- `lang="pt-BR"`, `color-scheme` e `theme-color` declarados; favicon embutido
  como data URI (evita o 404 de `/favicon.ico` no log).

O layout é responsivo — sem rolagem horizontal a partir de 380 px.

O servidor encerra junto com o pipeline — manter no ar travaria execuções em
cron. O estado final permanece no JSON e pode ser reaberto depois com qualquer
servidor estático.

Para acesso a partir do host em execução conteinerizada, veja
[Uso com Docker](#uso-com-docker) — são **duas** coisas, não uma:
publicar a porta **e** mandar o servidor ouvir fora do loopback.

---

## Webhooks

```bash
python etl.py complete --month 07/2026 --webhook-url https://exemplo.com/hook
export PIPELINE_WEBHOOK_URL=https://exemplo.com/hook
```

A flag tem prioridade sobre a variável de ambiente.

### Eventos

| `event` | Quando |
|---|---|
| `pipeline_started` | início da execução |
| `step_started` | início de cada etapa |
| `step_completed` | etapa concluída com sucesso |
| `step_failed` | etapa falhou |
| `pipeline_completed` | fim da execução, sem falhas |
| `pipeline_failed` | fim da execução com falha |

### Payload

`POST` com `Content-Type: application/json`:

```json
{
  "event": "step_completed",
  "run_id": "3f2a1b8c-5d6e-4f70-8a91-2b3c4d5e6f70",
  "reference_period": "2026-07",
  "step": {
    "name": "download_arquivos",
    "status": "success",
    "started_at": "2026-07-25T14:32:11-03:00",
    "finished_at": "2026-07-25T14:35:02-03:00",
    "error": null
  },
  "timestamp": "2026-07-25T14:35:02-03:00"
}
```

Nos eventos de pipeline (`pipeline_started`, `pipeline_completed`,
`pipeline_failed`) o campo `step` é omitido.

### Resiliência

**Uma falha de webhook nunca interrompe o pipeline.** Timeout de 5 s, nenhuma
retentativa, toda exceção capturada e registrada no log. Um destino fora do ar
gera aviso nas três primeiras falhas e depois a cada 25, para não inundar o log
de uma carga de horas.

---

## Uso com Docker

O pipeline roda em container em produção, e as três capacidades funcionam lá —
desde que três detalhes sejam observados. Todos foram verificados executando a
imagem de verdade.

### 1. O volume do estado é obrigatório para a retomada

O estado vive em `/app/data/state` **dentro** do container. Sem montá-lo como
volume, ele morre junto com o container e cada execução recomeça do zero — o
que anula justamente a razão de existir do checkpoint.

```bash
-v "$PWD/data/state:/app/data/state"     # ← sem isto, não há retomada
```

Comportamento observado sem o volume: nenhuma linha `ETAPA JÁ CONCLUÍDA,
PULANDO` na segunda execução. Com o volume, um container **novo** (após
`docker rm`) reconhece o progresso:

```
ℹ️ ESTADO ENCONTRADO PARA 2026-07: 1/11 ETAPAS JÁ CONCLUÍDAS
ℹ️ ETAPA JÁ CONCLUÍDA, PULANDO: schema_init
```

> **Nunca monte `/app/data` inteiro** para resolver isso — o diretório contém
> os CSVs do IBGE embutidos na imagem, que seriam mascarados pelo bind mount.
> Monte os subdiretórios (`downloads`, `logs`, `state`) individualmente.

### 2. Dashboard: publicar a porta **e** ouvir em 0.0.0.0

São dois requisitos independentes; faltando qualquer um, o acesso do host
falha:

| Requisito | Como | Se faltar |
|---|---|---|
| Publicar a porta | `-p 3010:3010` | conexão recusada no host |
| Ouvir fora do loopback | `--host 0.0.0.0` | o servidor só aceita conexões de dentro do container |

```bash
docker run -d --name cnpj-run \
  -p 3010:3010 \
  --env-file .env \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  -v "$PWD/data/state:/app/data/state" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 07/2026 --parallel \
           --serve --host 0.0.0.0
```

Acesse `http://localhost:3010`. O navegador pede usuário e senha — pegue a
senha gerada no log do container:

```bash
docker logs cnpj-run 2>&1 | grep -A1 "DASHBOARD DISPONÍVEL"
```

Para definir a sua e não depender do log, use
`-e PIPELINE_DASHBOARD_PASSWORD=minha-senha`.

Para outra porta no host, mude só o lado esquerdo (`-p 8080:3010`); para mudar
dentro do container, use `--port` e ajuste os dois lados.

> ⚠️ **Com `--network host` (como na execução mensal em produção) o `-p` é
> ignorado** e `--host 0.0.0.0` faz o dashboard ouvir em **todas** as
> interfaces da máquina, inclusive a pública. O dashboard não tem
> autenticação e exibe mensagens de erro do pipeline. Nesse modo, ou proteja a
> porta 3010 no firewall, ou deixe o padrão `127.0.0.1` e acesse por túnel
> SSH:
>
> ```bash
> ssh -N -L 3010:127.0.0.1:3010 root@servidor    # do seu computador
> ```

### 3. Webhook: de onde o container enxerga o destino

O container tem sua própria rede; a URL precisa ser alcançável **de dentro
dele**.

| Destino | URL a usar |
|---|---|
| Serviço externo (Slack, n8n na nuvem) | a URL normal — funciona direto |
| Outro container na mesma rede | `http://<nome-do-serviço>:porta/hook` |
| Um servidor no **host** (Docker Desktop, macOS/Windows) | `http://host.docker.internal:8899/hook` |
| Um servidor no **host** (Docker no Linux) | `http://host.docker.internal:8899/hook` **+** `--add-host=host.docker.internal:host-gateway` |
| Com `--network host` | `http://localhost:8899/hook` |

```bash
docker run -d --name cnpj-run \
  --env-file .env \
  -v "$PWD/data/state:/app/data/state" \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  -e PIPELINE_WEBHOOK_URL=https://n8n.exemplo.com/webhook/cnpj \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  complete --month 07/2026 --parallel
```

`PIPELINE_WEBHOOK_URL` no `--env-file` é a forma mais prática em container —
evita repetir a URL no comando. A flag `--webhook-url` continua tendo
prioridade se ambas forem usadas.

Um destino inalcançável **não interrompe o pipeline**: gera aviso no log e a
carga segue.

### Com Docker Compose

O `docker-compose.yaml` já traz o volume de estado, a porta e as variáveis:

```bash
# Dashboard em http://localhost:3010 durante a carga
docker compose run --rm --service-ports etl \
    complete --month 07/2026 --parallel --serve --host 0.0.0.0
```

> `docker compose run` **não publica portas por padrão** — daí o
> `--service-ports`. Com `docker compose up` a publicação é automática.

Variáveis reconhecidas no `.env` do compose:

| Variável | Padrão | Para quê |
|---|---|---|
| `FORWARD_DASHBOARD_PORT` | `3010` | porta no host |
| `PIPELINE_PORT` | `3010` | porta dentro do container |
| `PIPELINE_WEBHOOK_URL` | vazio | destino das notificações |
| `PIPELINE_DASHBOARD_PASSWORD` | gerada | senha do dashboard |
| `PIPELINE_MAX_ATTEMPTS` | `3` | tentativas por etapa |
| `PIPELINE_STATE_DIR` | `/app/data/state` | onde o estado é gravado |

### Permissões do volume

O container roda como `etluser` (uid 1000). Se `data/state` for criado no host
pelo `root`, o pipeline não conseguirá gravar. O serviço
`etl-init-permissions` do compose já ajusta os três diretórios; em `docker
run`, faça uma vez:

```bash
mkdir -p data/{downloads,logs,state}
sudo chown -R 1000:1000 data/downloads data/logs data/state
```

### Verificação rápida

```bash
# o estado está no host (e não só dentro do container)?
ls -l data/state/

# o dashboard responde do host?
curl -s http://localhost:3010/state.json | head -c 200

# a retomada funciona? remova o container e repita o comando:
docker rm -f cnpj-run && docker run ... complete --month 07/2026
# deve aparecer: "ETAPA JÁ CONCLUÍDA, PULANDO: ..."
```

---

## Referência das flags

Disponíveis em `download`, `complete` e em todos os subcomandos `db`:

| Flag | Variável de ambiente | Padrão | O que faz |
|---|---|---|---|
| `--force`, `--force-restart` | — | desligado | ignora o estado e reexecuta tudo (com backup `.bak`) |
| `--no-state` | — | desligado | desliga o checkpoint/retomada |
| `--reference-period` | — | inferido | período dos dados (`AAAA-MM` ou `MM/AAAA`) |
| `--max-attempts` | `PIPELINE_MAX_ATTEMPTS` | `3` | tentativas por etapa (`0` = ilimitado) |
| `--serve` | — | desligado | sobe o dashboard |
| `--port` | `PIPELINE_PORT` | `3010` | porta do dashboard |
| `--host` | — | `127.0.0.1` | interface do dashboard |
| `--dashboard-password` | `PIPELINE_DASHBOARD_PASSWORD` | gerada | senha do dashboard (Basic Auth) |
| `--dashboard-user` | `PIPELINE_DASHBOARD_USER` | `pipeline` | usuário do dashboard |
| `--no-auth` | — | desligado | serve o dashboard sem autenticação |
| `--webhook-url` | `PIPELINE_WEBHOOK_URL` | — | destino das notificações |

Outras variáveis: `PIPELINE_STATE_DIR` (padrão `data/state`) e
`PIPELINE_REFRESH_SECONDS` (intervalo inicial do polling, padrão `6`).

---

## Receita: retomar uma execução interrompida

```bash
# 1. O que já foi concluído?
python -c "
import json;s=json.load(open('data/state/pipeline_state_2026-07.json'))
print(s['status'], s['run_id'])
[print(f\"  {x['status']:8} {x['name']}\") for x in s['steps']]"

# 2. Retomar — as etapas concluídas são puladas automaticamente
python etl.py complete --month 07/2026

# 3. Acompanhar pelo navegador
python etl.py complete --month 07/2026 --serve
```

Para refazer **apenas** uma etapa, use o subcomando isolado — ele atualiza a
etapa correspondente no estado sem tocar nas demais:

```bash
python etl.py db views create      # refaz só as materialized views
python etl.py db fk                # refaz só as foreign keys
```

---

## Testes

```bash
python3 tests/test_observabilidade.py   # estado, retomada, webhooks, dashboard
python3 tests/test_pipeline_stats.py    # tabela de stats (sobe Postgres em Docker)
python3 tests/test_e2e_retomada.py      # CLI real ponta a ponta
```

Os dois últimos sobem um PostgreSQL descartável via Docker e o removem ao
final; `PGTEST_DSN` aponta para um banco já existente, se preferir.
