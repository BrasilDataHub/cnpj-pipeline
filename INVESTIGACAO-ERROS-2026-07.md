# Investigação — erros da execução de 25/07/2026 (`cnpj-run-2026-07`)

**Data da investigação:** 25/07/2026
**Log analisado:** `data/logs/etl-2026-07-25.log` (366 linhas)
**Execução:** iniciada 07:49:47, abortada 14:32:57 — **6h43m10s** de processamento.

> ## ✅ Status: corrigido e retomado
>
> A investigação foi feita sem alterar nada. A correção foi aplicada em seguida,
> com autorização, e **confirmada empiricamente**:
>
> - `/dev/shm` do container Postgres elevado de **64 MB → 2 GB** via mount
>   `tmpfs` no serviço Swarm (`docker service update --mount-add …`).
> - Pipeline retomado com `db views create` — **sem reprocessar nada**.
> - **As 14 etapas concluíram em 19m23s, exit code 0.** A MV 08, que matava o
>   pipeline, levou 43,6 s. Estado final validado no banco: **13 materialized
>   views** + a função `refresh_all_mvs()`.
> - As 6h43 da carga original (downloads, tabelas, 40 índices,
>   `busca_estabelecimento` com 72.318.968 linhas) foram integralmente
>   preservadas.
> - A imagem `ghcr.io/brasildatahub/postgres:17` ganhou uma guarda de start
>   ([`shm-guard.sh`](../infra/postgres/shm-guard.sh)) que detecta `/dev/shm`
>   insuficiente e degrada o paralelismo em vez de deixar a query quebrar.
>   Documentação de deploy por plataforma em
>   [`infra/postgres/docs/deploy.md`](../infra/postgres/docs/deploy.md).
>
> - **Limite de memória de 14G aplicado** após a conclusão das views, fechando a
>   outra metade do perfil que o Swarm havia descartado.
> - **As 2 FKs de `cod_pais` criadas** (§3), depois de completar a tabela `pais`
>   com os 3 códigos órfãos.
>
> **Estado final validado no banco:**
>
> ```
>  mvs | fks        conf_invalida     max_parallel_workers_per_gather = 4
> -----+-----      ---------------    (não degradado — /dev/shm suficiente)
>   13 |  18                    0
> ```
>
> **Pendência que permanece — e é a mais importante:** o mount `tmpfs` e o
> limite foram aplicados via `docker service update`, ou seja, vivem **no
> serviço Swarm, não na definição que o Dokploy guarda**. Um redeploy pelo
> painel os desfaz. A correção durável é migrar o Postgres para um recurso do
> tipo *Compose stack*, onde ambos ficam versionados no YAML — ver
> [`deploy.md`](../infra/postgres/docs/deploy.md#4-dokploy--shm_size-é-descartado-usa-swarm-por-baixo).

---

## Sumário executivo

| # | Erro | Gravidade | Causa raiz | Retomável sem reprocessar? |
|---|---|---|---|---|
| 1 | `DiskFull: could not resize shared memory segment` na MV 08 | **Bloqueante** | `/dev/shm` do container Postgres = **64 MB** (default do Docker). O perfil `dedicada-16gb` prescreve **2 GB**, mas o Swarm/Dokploy **não aplica `shm_size`** | ✅ Sim |
| 2 | 2 FKs rejeitadas (`fk_estabelecimento_3`, `fk_socio_2`) | Baixa | **5 linhas** com `cod_pais` ausente da tabela de domínio da RFB (`042`, `693`, `755`) | ✅ Sim |
| 3 | Avisos de mapeamento SIAFI→IBGE | Informativa | 99,4% são UF `EX` (exterior) — esperado. ~1.063 misses reais | ✅ Sim (não bloqueia) |

**A causa raiz do erro principal não é falta de RAM.** A máquina tem 25 GB disponíveis e ociosos. É um teto de 64 MB em `/dev/shm` que o deploy nunca elevou.

> ⚠️ **Não re-executar `complete`.** Foi o comando usado nesta rodada, e ele começa derrubando as tabelas (`TABELAS ANTERIORES REMOVIDAS`, linha 19 do log). Isso descartaria as 6h43 já processadas. A retomada correta é `db views create` — detalhada na [seção 5](#5-plano-de-retomada).

---

## 1. Ambiente observado

Evidências coletadas no servidor `152.53.36.62` em 25/07/2026.

### Host
```
Netcup RS 4000 G12 — 31 Gi RAM, 12 cores AMD EPYC 9645
/dev/vda4   1007G   153G usados   814G livres  (16%)
Mem:  31Gi total | 6.1Gi usados | 25Gi disponíveis
/dev/shm (host): 16G — 0% usado
```

### Container Postgres (`databases-postgres-krjkec.1.jgbs…`)
```
$ docker exec <pg> df -h /dev/shm
Filesystem      Size  Used Avail Use% Mounted on
shm              64M  1.1M   63M   2% /dev/shm      ← ⚠️ AQUI

$ docker inspect <pg> --format 'ShmSize={{.HostConfig.ShmSize}} Memory={{.HostConfig.Memory}}'
ShmSize=67108864  Memory=0

$ docker service inspect databases-postgres-krjkec --format '{{json .Spec.TaskTemplate.Resources}}'
{"Limits":{},"Reservations":{}}                     ← ⚠️ sem limite de memória
```

O `ContainerSpec` do serviço contém **apenas as 29 envs `PG_*`** do perfil `dedicada-16gb`. Não há `shm_size`, não há limite de memória.

### O que o repositório de infra prescreve

`infra/postgres/docker-compose.yml:29`:
```yaml
shm_size: ${PG_SHM_SIZE:-1gb}
```

`infra/postgres/docs/perfis.md:219` — tabela **Recursos do container**:

| | 8gb | 16gb | 32gb | 64gb | 128gb |
|---|---|---|---|---|---|
| Limite de memória | 7G | **14G** | 28G | 56G | 120G |
| `shm_size` | 1gb | **2gb** | 4gb | 4gb | 8gb |

E o texto imediatamente acima dessa tabela (`perfis.md:210-212`):

> Não são envs do Postgres — são configuração do serviço no deploy, e viajam **junto** com o bloco: subir `shared_buffers` sem subir o limite aproxima o OOM-killer.

`perfis.md:774-777` documenta o modo de falha **exatamente como ele ocorreu**:

> **`shm_size` e paralelismo.** Com `dynamic_shared_memory_type=posix`, os segmentos de memória compartilhada dos workers saem de `/dev/shm`. `shm_size` insuficiente derruba queries paralelas com erro obscuro; os valores da tabela de recursos consideram `max_parallel_workers × work_mem × hash_mem_multiplier`.

**Conclusão:** o perfil `dedicada-16gb` foi aplicado **pela metade** — o bloco de envs foi colado, os dois recursos de container que deveriam viajar junto (`shm_size` 2gb e limite 14G) ficaram para trás.

---

## 2. Erro principal — `DiskFull` na criação das materialized views

### Trecho do log

```
🕒 14:32:53 |⏱️ 06:43:06 |ℹ️ [08/14] EXECUTANDO: 08_mv_regime_tributario_cidade.sql...
🕒 14:32:57 |⏱️ 06:43:10 |❌ [08/14] ERRO EM 08_mv_regime_tributario_cidade.sql:
   could not resize shared memory segment "/PostgreSQL.3316007114" to 16814080 bytes:
   No space left on device
🕒 14:32:57 |⏱️ 06:43:10 |❌ ERRO AO CRIAR MATERIALIZED VIEWS: [idem]
```

```
psycopg2.errors.DiskFull: could not resize shared memory segment ...
  File "/app/src/rfb_cnpj_etl/db/postgres_builder.py", line 749, in create_materialized_views
    cur.execute(sql_content)
```

Note que falhou em **4 segundos** — não é esgotamento progressivo, é recusa na alocação inicial.

### Causa raiz

A mensagem `No space left on device` é enganosa: **não é o disco**. O disco tem 814 GB livres. É o `tmpfs` montado em `/dev/shm` **dentro do container Postgres**, limitado a 64 MB.

A cadeia causal:

1. `generate-config.sh:129` fixa `dynamic_shared_memory_type = posix`. Com isso, os segmentos DSM (Dynamic Shared Memory) que os workers paralelos usam para se comunicar são **arquivos em `/dev/shm`**.
2. A MV 08 é a **primeira do conjunto que faz JOIN entre duas tabelas gigantes**. `EXPLAIN` da query real, executado agora no banco:

```
Finalize GroupAggregate
  ->  Gather Merge
        Workers Planned: 4
        ->  Partial HashAggregate
              ->  Parallel Hash Left Join                    ← hash table #2 em /dev/shm
                    Hash Cond: (emp.cnpj_basico = s.cnpj_basico)
                    ->  Parallel Hash Left Join              ← hash table #1 em /dev/shm
                          Hash Cond: (e.cnpj_basico = emp.cnpj_basico)
                          ->  Parallel Seq Scan on estabelecimento e
                          ->  Parallel Hash
                                ->  Parallel Seq Scan on empresa emp
                    ->  Parallel Hash
                          ->  Parallel Seq Scan on simples s
```

3. **`Parallel Hash` aloca a hash table em memória compartilhada — ou seja, em `/dev/shm`.** São duas, aninhadas e simultâneas: uma sobre `empresa` (~65M linhas), outra sobre `simples` (~45M linhas).

4. O orçamento que o Postgres se concede por nó `Parallel Hash` é `work_mem × hash_mem_multiplier × participantes`:

   ```
   32 MB × 2.0 × (4 workers + 1 líder) = 320 MB   por hash table
   duas hash tables simultâneas         = até 640 MB de /dev/shm
   disponível                           =        64 MB
   ```

   O segmento cujo `resize` falhou pedia 16.814.080 bytes (~16 MB) — um chunk de crescimento do DSA que, somado ao que já estava alocado, não coube nos 64 MB.

### Por que as MVs 01–07 passaram

Não é coincidência de tamanho — é o **tipo de JOIN**:

| MV | JOINs | Resultado |
|---|---|---|
| 01 | `estabelecimento` ⨝ `ibge_estado` (27 linhas) | ✅ 131.7s |
| 02–05, 07 | agregação sobre `estabelecimento`, sem JOIN grande | ✅ |
| 06 | `estabelecimento` ⨝ `cnae` ⨝ `ibge_cidade` (dimensões pequenas) | ✅ 89.6s |
| **08** | `estabelecimento` ⨝ **`empresa`** ⨝ **`simples`** | ❌ **falhou** |

MVs 01–07 fazem hash apenas sobre **tabelas de dimensão** (dezenas a milhares de linhas) — cabem folgadamente em 64 MB. A 08 é a primeira a fazer hash sobre tabelas de dezenas de milhões.

### ⚠️ Não é só a MV 08

As MVs restantes têm o mesmo padrão. Corrigir só para a 08 e re-rodar falharia de novo:

| MV | JOIN pesado | Prognóstico com 64 MB |
|---|---|---|
| 09 `mv_porte_cidade` | `estabelecimento` ⨝ `empresa` | ❌ falha |
| 10 `mv_stats_natureza_juridica_estado` | `natureza_juridica` ⨝ `empresa` | ⚠️ risco |
| 11 `mv_stats_natureza_juridica_municipio` | `natureza_juridica` ⨝ `empresa` ⨝ `estabelecimento` | ❌ falha |
| 12 `mv_stats_natureza_juridica` | `natureza_juridica` ⨝ `empresa` ⨝ `estabelecimento` | ❌ falha |
| 13 `mv_stats_natureza_juridica_cnae` | `empresa` ⨝ `estabelecimento` ⨝ `cnae` | ❌ falha |

### Por que não acontecia na máquina de 8 GB

Este é o ponto que o relato levantou como estranho, e a resposta é contra-intuitiva mas direta: **subir de perfil aumenta o consumo de `/dev/shm`, enquanto o `/dev/shm` continuou em 64 MB nos dois casos.**

```
perfil dedicada-8gb :  16 MB × 2.0 × (2 workers + 1) =  96 MB por hash table
perfil dedicada-16gb:  32 MB × 2.0 × (4 workers + 1) = 320 MB por hash table   (3,3×)
```

Hipóteses ranqueadas para a máquina antiga não falhar (não tenho acesso a ela para confirmar):

1. **Mais provável — o `/dev/shm` lá era maior.** Se o Postgres antigo subia por `docker compose` comum (não Swarm), o `shm_size: ${PG_SHM_SIZE:-1gb}` do `infra/postgres/docker-compose.yml` **era aplicado**: 1 GB de `/dev/shm`, 16× o teto atual. Docker Swarm — que o Dokploy usa aqui — **não suporta `shm_size` em serviços**, e o silencioso resultado é o default de 64 MB. Essa é a diferença estrutural entre os dois ambientes.
2. **Contribuinte — menos paralelismo.** Com `max_parallel_workers_per_gather=2` e `work_mem=16MB`, o planner tinha custo maior para plano paralelo e orçamento de hash 3,3× menor; parte das queries provavelmente caía em `Hash Join` não-paralelo, que usa **memória privada do backend**, não `/dev/shm`.

Em resumo: **o erro não apareceu apesar da máquina maior — apareceu por causa dela.** O perfil mais robusto pediu mais memória compartilhada contra um teto que ninguém subiu.

### Recomendação de correção

**Correção definitiva — elevar `/dev/shm` para 2 GB** (valor do perfil `dedicada-16gb`).

Como o Swarm ignora `shm_size`, o caminho suportado é um mount `tmpfs` explícito. Verificado que a flag existe nesta instalação (`docker service update --mount-add`):

```bash
docker service update \
  --mount-add type=tmpfs,destination=/dev/shm,tmpfs-size=2147483648 \
  databases-postgres-krjkec
```

Dois pontos de atenção:
- Isso **recria o container** (restart do Postgres, ~segundos). O volume de dados é persistente — **nenhum dado e nenhum progresso do ETL é perdido**.
- O serviço é gerenciado pelo **Dokploy**. Um `docker service update` manual tende a ser sobrescrito no próximo redeploy — o ajuste deve ser registrado na configuração do serviço no Dokploy (seção de mounts/advanced) para persistir.

Aproveitar o mesmo restart para aplicar o **limite de memória de 14G** que também ficou faltando (`--limit-memory 14G`), fechando a lacuna do perfil.

**Mitigação alternativa, sem restart e sem redeploy** — caso se prefira rodar as views já:

```sql
ALTER DATABASE dados_cnpj SET max_parallel_workers_per_gather = 0;
-- executar `db views create`
ALTER DATABASE dados_cnpj RESET max_parallel_workers_per_gather;
```

Com paralelismo desligado, o `Hash Join` passa a ser serial e a hash table vai para a **memória privada do backend** (`work_mem × hash_mem_multiplier`, com spill para arquivos temporários em disco — há 814 GB livres). Funciona, mas é sensivelmente mais lento nas MVs grandes. É uma saída de contingência, não a correção.

> Uma terceira via — `SET enable_parallel_hash = off` — mantém o paralelismo e evita o DSM, mas faz **cada worker** construir sua própria cópia da hash table sobre `empresa`/`simples`. Multiplica o consumo de RAM privada por 5 e tende a derramar em disco. Não recomendo sem o limite de memória do container aplicado.

### ✅ Retomada sem reprocessar

**Sim, totalmente retomável.** Nada do que foi construído em 6h43 é perdido:

- 37 arquivos baixados e validados — intactos em `data/downloads/2026-07`;
- carga das tabelas, PKs, 40 índices, conversão LOGGED — concluídos;
- `busca_estabelecimento` com 72.318.968 linhas e seus 4 índices — concluída (log 14:24:11);
- 7 das 14 MVs — já materializadas no banco.

A etapa de views é **idempotente por construção**: cada arquivo SQL começa com `DROP MATERIALIZED VIEW IF EXISTS … CASCADE`. Re-executar `db views create` recria as 7 primeiras (≈8m40s, somando os tempos do log) e prossegue da 08 em diante. Verificado no banco agora:

```
 matviewname              |   tam
--------------------------+---------
 mv_abertura_periodo      | 944 kB
 mv_stats_cidade_situacao | 3192 kB
 mv_stats_cnae            | 384 kB
 mv_stats_cnae_estado     | 4176 kB
 mv_stats_estado          | 64 kB
 mv_stats_municipio       | 1032 kB
 mv_top_cnaes_cidade      | 112 MB
(7 rows)
```

---

## 3. Erro secundário — 2 chaves estrangeiras rejeitadas

### Trecho do log

```
🕒 13:52:24 |❌ [07/18] ERRO AO ADICIONAR FK 'fk_estabelecimento_3':
   insert or update on table "estabelecimento" violates foreign key constraint "fk_estabelecimento_3"
   DETAIL:  Key (cod_pais)=(042) is not present in table "pais".

🕒 13:55:08 |❌ [14/18] ERRO AO ADICIONAR FK 'fk_socio_2':
   insert or update on table "socio" violates foreign key constraint "fk_socio_2"
   DETAIL:  Key (cod_pais)=(693) is not present in table "pais".
```

As outras 16 FKs foram criadas. Confirmado no banco: 16 constraints do tipo `f` existem; faltam exatamente `fk_estabelecimento_3` e `fk_socio_2`.

### Causa raiz

**Inconsistência na origem — os próprios dados da Receita Federal.** A tabela de domínio `pais` (273 linhas, carregada do `PAISCSV` do mesmo mês) não contém os códigos `042`, `693` e `755`, mas eles aparecem nos dados cadastrais.

> ⚠️ **O log subestima o problema.** Ele reporta apenas `042` e `693`, porque
> `ALTER TABLE … ADD CONSTRAINT` aborta no **primeiro** valor violado de cada
> FK — o restante nunca chega a ser avaliado. O terceiro código (`755`, em
> `socio`) só apareceu no levantamento exaustivo feito na hora da correção.
> Lição: nunca dimensione esta classe de problema pelo que o log mostra.

O volume ainda é ínfimo — **5 linhas em ~117 milhões**, 3 códigos distintos:

```
     tabela      | cod_pais | linhas
-----------------+----------+--------
 estabelecimento | 042      |      1
 estabelecimento | 693      |      1
 socio           | 693      |      2
 socio           | 755      |      1
```

São **buracos na sequência da tabela de domínio** — os códigos vizinhos existem:

```
 040 | ANGOLA              690 | SAMOA                    754 | SUAZILANDIA
 041 | ANGUILLA            691 | SOMOA AMERICANA          755 |  ← ausente
 042 |  ← ausente          693 |  ← ausente               756 | AFRICA DO SUL
 043 | ANTIGUA E BARBUDA   695 | SAO CRISTOVAO E NEVES
                           697 | SAN MARINO
```

O padrão é típico de **códigos legados/descontinuados** que permanecem em cadastros antigos depois de saírem da tabela de domínio vigente. Não é defeito do pipeline: o ETL carregou fielmente o que a RFB publicou nos dois arquivos.

Um detalhe do comportamento atual: `enable_foreign_keys` (`postgres_builder.py:319-325`) trata apenas o código `42710` (constraint duplicada) como benigno; qualquer outro erro é **logado e a execução continua**. Por isso o pipeline seguiu — mas as duas FKs simplesmente **não existem** no banco hoje, silenciosamente.

### Recomendação de correção

Duas opções, ambas legítimas:

**Opção A — completar a tabela de domínio (preferida).** Inserir os códigos órfãos como placeholder antes de criar as FKs, tornando o ETL resiliente a essa classe de inconsistência:

```sql
INSERT INTO pais (cod_pais, nome_pais)
SELECT DISTINCT e.cod_pais, 'CODIGO NAO CONSTANTE NA TABELA RFB'
  FROM estabelecimento e LEFT JOIN pais p ON e.cod_pais = p.cod_pais
 WHERE e.cod_pais IS NOT NULL AND p.cod_pais IS NULL
UNION
SELECT DISTINCT s.cod_pais, 'CODIGO NAO CONSTANTE NA TABELA RFB'
  FROM socio s LEFT JOIN pais p ON s.cod_pais = p.cod_pais
 WHERE s.cod_pais IS NOT NULL AND p.cod_pais IS NULL;
```

Preserva a integridade referencial e é auto-adaptável — em meses futuros novos códigos órfãos serão absorvidos sem quebrar. O lugar natural para isso é `apply_patches` / `sql/prod_hygiene.sql`, **antes** de `enable_foreign_keys`.

**Opção B — criar as FKs como `NOT VALID`.** Passa a valer para linhas novas sem validar o histórico:

```sql
ALTER TABLE estabelecimento ADD CONSTRAINT fk_estabelecimento_3
  FOREIGN KEY (cod_pais) REFERENCES pais(cod_pais) NOT VALID;
```

Mais barato, mas deixa a base com constraints não validadas — prefiro A.

Independente da opção, vale ajustar `enable_foreign_keys` para **contabilizar e resumir as falhas ao final** em vez de só logar no meio de 18 linhas. Foi o que fez essas duas passarem despercebidas até agora.

### ✅ Retomada sem reprocessar

**Sim.** As duas FKs são criadas isoladamente com `ALTER TABLE`, sem tocar nos dados. Também é possível re-rodar a etapa inteira via `db fk` — as 16 já existentes retornam `42710` e o próprio código as **pula** com "FK JÁ EXISTE" (`postgres_builder.py:321-322`). Custo: alguns minutos de validação das duas constraints. Nenhum reprocessamento.

---

## 4. Erros menores — avisos de mapeamento SIAFI → IBGE

### Trecho do log

```
🕒 08:51:51 |⚠️ REGIÃO IBGE NÃO ENCONTRADA PARA UF EX
🕒 08:51:51 |⚠️ CÓDIGO SIAFI SEM MAPEAMENTO IBGE: 9707
🕒 08:51:51 |⚠️ CÓDIGO SIAFI SEM MAPEAMENTO IBGE: 1182
🕒 08:51:52 |⚠️ CÓDIGO SIAFI SEM MAPEAMENTO IBGE: 9707
```

### Causa raiz

Emitidos por `utils/ibge_lookup.py:197,201`. **Atenção: o log subestima o fenômeno** — o contador `_misses_notificados` limita a 5 mensagens no total (`ibge_lookup.py:199-201`), então as 4 linhas acima não representam 4 ocorrências.

Medido diretamente no banco:

```
 sem_cidade_ibge | sem_estado_ibge | sem_regiao_ibge |  total
-----------------+-----------------+-----------------+----------
          173178 |          172621 |          172621 | 72318968
```

Quebrando por UF:

```
 uf | count
----+--------
 EX | 172115     ← 99,4% — estabelecimentos no exterior
 MT |    559
 SP |    192
 DF |    120
 RJ |     71
 PR |     32   (…)
```

Dois fenômenos distintos, misturados sob o mesmo aviso:

1. **UF = `EX` (172.115 registros — 99,4%): comportamento correto, não é erro.** Estabelecimentos sediados no exterior não têm — nem deveriam ter — município IBGE. O aviso é ruído.
2. **~1.063 registros em UF brasileira: misses reais** de mapeamento SIAFI→IBGE, concentrados em MT (559). Padrão compatível com municípios recém-criados/desmembrados, ou códigos SIAFI ausentes da tabela de localidades em cache.

**Impacto real:** 0,0015% dos estabelecimentos ficam com `cod_cidade_ibge` nulo e portanto **não aparecem nas MVs agregadas por cidade**. Estatisticamente irrelevante, mas é uma perda silenciosa.

### Recomendação de correção

- **Silenciar `EX` explicitamente** em `lookup_codigos` — tratar exterior como caso esperado, não como miss. Elimina 99,4% do ruído e faz os avisos restantes significarem algo.
- **Trocar o teto de 5 mensagens por um resumo agregado** ao final da carga (`N códigos SIAFI sem mapeamento, M registros afetados`). O limite atual esconde a dimensão real do problema — foi por isso que 173 mil registros apareceram como 4 linhas de log.
- **Auditar os 559 casos de MT** contra a tabela de municípios do IBGE vigente e atualizar o CSV de localidades.

### ✅ Retomada sem reprocessar

**Não se aplica como bloqueio** — são avisos, o pipeline seguiu normalmente. Porém, uma observação honesta: corrigir o *mapeamento* dos ~1.063 registros **exigiria recarregar `estabelecimento`**, porque `cod_cidade_ibge` é resolvido no momento da carga. Dado o impacto (0,0015%), **recomendo tratar isso no próximo ciclo mensal**, não agora. Não justifica reprocessar 6h43.

---

## 5. Plano de retomada

Ordem sugerida. Nenhum passo reprocessa download ou carga.

### ✅ Passo 0 — corrigir o `/dev/shm` (bloqueante) — **EXECUTADO**

```bash
docker service update \
  --mount-add type=tmpfs,destination=/dev/shm,tmpfs-size=2147483648 \
  databases-postgres-krjkec
```

Resultado verificado:

```
Filesystem      Size  Used Avail Use% Mounted on
tmpfs           2.0G  1.1M  2.0G   1% /dev/shm
```

> O `--limit-memory 14G` foi **deliberadamente adiado** para o Passo 3: aplicar
> um teto de memória logo antes da etapa mais pesada do pipeline trocaria um
> problema conhecido por um risco de OOM-kill no meio da criação das views.
>
> ⚠️ Registrar o mount na configuração do serviço no **Dokploy**, senão o
> próximo redeploy o desfaz. Ver
> [`infra/postgres/docs/deploy.md`](../infra/postgres/docs/deploy.md#4-dokploy--shm_size-é-descartado-usa-swarm-por-baixo).

### ✅ Passo 1 — completar a tabela `pais` e criar as 2 FKs — **EXECUTADO**

O `INSERT` da [seção 3](#recomendação-de-correção-1) absorveu **3 códigos**
(`INSERT 0 3` — incluindo o `755`, que o log nunca chegou a reportar). Em
seguida, `db fk` criou as duas constraints em 9 segundos, pulando as 16
existentes:

```bash
docker run --rm --name cnpj-fk-2026-07 --network host \
  --env-file .env \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db fk
```

As 16 FKs existentes são puladas automaticamente.

### ✅ Passo 2 — retomar as materialized views — **EXECUTADO E CONCLUÍDO**

```bash
cd /root/brasildatahub/cnpj-pipeline

docker run -d --name cnpj-views-2026-07 --network host \
  --env-file .env \
  -v "$PWD/data/downloads:/app/data/downloads" \
  -v "$PWD/data/logs:/app/data/logs" \
  ghcr.io/brasildatahub/cnpj-pipeline:latest \
  db views create
```

Recriou as 7 MVs existentes (≈8 min) e prosseguiu da 08 em diante. Trecho do log
da retomada — as duas MVs que antes eram impossíveis:

```
🕒 01:00:02 |ℹ️ [08/14] EXECUTANDO: 08_mv_regime_tributario_cidade.sql...
🕒 01:00:45 |✅ [08/14] CONCLUÍDO: 08_mv_regime_tributario_cidade.sql (43.6s)
🕒 01:00:45 |ℹ️ [09/14] EXECUTANDO: 09_mv_porte_cidade.sql...
🕒 01:01:14 |✅ [09/14] CONCLUÍDO: 09_mv_porte_cidade.sql (29.1s)
...
🕒 01:10:45 |✅ [12/14] CONCLUÍDO: 12_mv_stats_natureza_juridica.sql (348.6s)
🕒 01:11:29 |✅ [13/14] CONCLUÍDO: 13_mv_stats_natureza_juridica_cnae.sql (44.4s)
🕒 01:11:29 |✅ [14/14] CONCLUÍDO: 99_refresh_function.sql (0.0s)
🕒 01:11:29 |✅ TODAS AS MATERIALIZED VIEWS FORAM CRIADAS
🕒 01:11:29 |🏁 EXECUÇÃO FINALIZADA | POSTGRES | views-create
```

Validação no banco após a conclusão:

```
 mvs_criadas
-------------
          13          ← mv_abertura_periodo … mv_top_cnaes_cidade

     funcao
-----------------
 refresh_all_mvs
```

> 🚫 **Nunca** `complete` para retomar. `complete` → `initialize_schema()` → derruba todas as tabelas.

### ✅ Passo 3 — aplicar o limite de memória — **EXECUTADO**

```bash
docker service update --limit-memory 14G databases-postgres-krjkec
```

Resultado: `{"Limits":{"MemoryBytes":15032385536},"Reservations":{}}` — 14 GiB,
o valor do perfil `dedicada-16gb`.

> **Ao conferir, use `df`, não `docker inspect`.** Com o `/dev/shm` vindo de um
> mount `tmpfs`, `HostConfig.ShmSize` continua reportando `67108864` (o default
> que o Swarm ignorou) mesmo com o tmpfs de 2 GB ativo. O que vale é
> `docker exec <c> df -h /dev/shm` e a lista de mounts.

### Validação final

```sql
SELECT count(*) FROM pg_matviews WHERE matviewname LIKE 'mv_%';   -- esperado: 13
SELECT count(*) FROM pg_constraint WHERE contype = 'f';            -- esperado: 18
SELECT sourceline, name, setting, applied, error
  FROM pg_file_settings WHERE NOT applied OR error IS NOT NULL;    -- deve voltar vazio
```

E a [verificação pós-deploy](../infra/postgres/docs/deploy.md#verificação-pós-deploy)
do repositório de infra, que cobre `/dev/shm`, o log do `shm-guard`, as envs e o
limite de memória.

---

## 6. Dimensionamento do Postgres — análise

### O incidente foi causado por subdimensionamento de RAM? Não.

Vale separar os dois assuntos, porque é fácil concluir a coisa errada aqui:

- **O erro foi um teto de 64 MB em `/dev/shm`** — um recurso do container que o perfil prescreve em 2 GB e que o deploy nunca aplicou. Com 2 GB de `/dev/shm`, esta mesma execução teria concluído no perfil de 16 GB **e também no de 8 GB**.
- Durante a falha havia **25 GB de RAM disponíveis e ociosos** no host. RAM não foi o gargalo em momento algum.

**Portanto: manter o perfil `dedicada-16gb` não foi o erro.** Aplicá-lo pela metade foi.

### O perfil de 16 GB está coerente com a intenção declarada

A intenção é dividir a máquina com Redis e Meilisearch. A tabela **Combinações prováveis** (`perfis.md:582`) endossa exatamente essa escolha:

| Cenário | Perfil PG | Vizinhos | RAM host mínima | Plano |
|---|---|---|---|---|
| Projeto médio consolidado | `dedicada-16gb` | redis `cache-512mb` + meili `busca-4gb` | 21 GB | **32 GB** |

O RS 4000 G12 com 32 GB é o plano indicado para esse cenário. **A escolha do perfil está correta pela documentação da própria org.**

### A tensão real: o volume da base

Há, porém, um descompasso que o incidente expôs de lado:

```
Tamanho atual de dados_cnpj:  127 GB
busca_estabelecimento:        72.318.968 linhas + 4 índices (2 deles GIN/trgm)
shared_buffers:               5 GB
```

`perfis.md:102-104` é direto sobre isso:

> Base Empresarial (116 GB, índices de busca de dezenas de GB) precisa de `dedicada-64gb` ou superior para servir busca textual com working set em RAM.

A base já está em **127 GB** — acima dos 116 GB de referência. Com `shared_buffers` de 5 GB e ~25 GB de page cache, o working set de busca textual **não cabe em RAM**. Isso não causou o erro de hoje, mas define o teto de desempenho da busca em produção.

### Recomendações, em ordem de prioridade

**1. Aplicar os recursos de container que faltam — agora.** `shm_size` 2 GB e limite 14 GB. É a correção do incidente e custa um restart. Sem isso, qualquer perfil falha do mesmo jeito.

**2. ✅ Corrigir o processo de deploy, não só este deploy — FEITO.** A causa estrutural é que o Dokploy/Swarm aceita as envs `PG_*` e **descarta silenciosamente** `shm_size`. Enquanto o perfil fosse aplicado copiando só o bloco de envs, a lacuna se repetiria a cada projeto novo. Foi tratado no repositório `infra` em duas frentes:

- **A imagem passou a se defender.** [`shm-guard.sh`](../infra/postgres/shm-guard.sh) roda no start, compara o `/dev/shm` real com o pico que o perfil exige e, por default, reduz `max_parallel_workers_per_gather` até caber — com um banner no log explicando a causa e a correção por plataforma. `PG_SHM_PREFLIGHT=fail` aborta o start em vez de subir degradado. Coberto por [testes](../infra/postgres/test/shm-guard.test.sh).
- **A documentação ganhou o que faltava:** [`docs/deploy.md`](../infra/postgres/docs/deploy.md) (receitas para Compose, `docker run`, Swarm, Dokploy, Coolify e Kubernetes + verificação pós-deploy) e [`docs/troubleshooting.md`](../infra/postgres/docs/troubleshooting.md). O aviso também foi propagado para `perfis.md`, os READMEs e os composes.

É um bug de deploy e de documentação, não de tuning.

**3. Sobre continuar compartilhando a máquina com Redis e Meilisearch — sim, mas com uma ressalva concreta.**

Um dado relevante: **Redis e Meilisearch ainda não existem neste host.** Verificado — só há Postgres, Traefik e o container do pipeline. O Postgres está limitado a um orçamento de 16 GB numa máquina de 32 GB para conviver com vizinhos que ainda não foram implantados, e hoje usa 6,1 GB.

- **Redis:** compartilhar é tranquilo. `cache-512mb` custa 1 GB de limite e 1 vCPU. Sem objeção.
- **Meilisearch:** aqui a documentação da org já antecipa o problema (`perfis.md:587-591`), e concordo com ela:

  > em base >100 GB com busca textual, **separe o Meilisearch em outra máquina**. Não é questão de RAM — a indexação do Meili despeja o page cache do Postgres, que é exatamente o ativo pelo qual se paga. O sintoma é confuso ("a busca ficou lenta *depois* da reindexação").

  Com uma base de 127 GB dependendo de page cache, uma reindexação do Meilisearch tocando vários GB vai evict as páginas quentes do Postgres. O efeito é degradação intermitente e difícil de diagnosticar.

**Recomendação:** manter **Postgres + Redis** neste host e mover **Meilisearch para outra máquina**. Isso libera o orçamento do Postgres de 16 GB para ~28 GB (perfil `dedicada-32gb`, limite 28G, `shm_size` 4gb, `work_mem` 48MB), aproveitando a RAM hoje ociosa e ampliando o page cache disponível para a busca — que é o gargalo real da base.

Se a decisão for manter os três juntos, o perfil de 16 GB continua defensável — mas então convém agendar as reindexações do Meilisearch fora da janela de pico, e tratar a lentidão pós-reindexação como custo conhecido, não como regressão.

**4. Revisar o paralelismo à luz do `/dev/shm` escolhido.** A regra da doc (`max_parallel_workers × work_mem × hash_mem_multiplier`) merece ser checada sempre que se mexer em `work_mem` ou nos workers. Com `shm_size` 2 GB e os valores atuais (320 MB por hash table, duas simultâneas ≈ 640 MB de pico), há folga confortável — mas é uma folga que encolhe se `work_mem` subir.

---

## 7. Observações adicionais (fora do log)

Encontradas ao cruzar a configuração do infra com o estado real do servidor. Não têm relação com a falha, mas são desvios reais:

**Porta do Postgres exposta em `0.0.0.0`:**
```
LISTEN 0 4096 0.0.0.0:15432 users:(("docker-proxy",...))
```
O compose de referência do infra (`perfis.md:836-838`) é explícito em sentido contrário:
> ```yaml
> ports:
>   # exclusivamente o IP privado — nunca 0.0.0.0
>   - "${PRIVATE_IP:?defina PRIVATE_IP}:5432:5432"
> ```
O banco está acessível pela internet pública com autenticação por senha. Recomendo restringir ao IP privado ou proteger por firewall.

**Serviço sem limite de memória:** `Resources: {"Limits":{},"Reservations":{}}`. Sem o limite de 14G do perfil, o Postgres não tem cerca — em pico de carga concorrente pode pressionar o host inteiro (que não tem swap). Corrigível no mesmo restart do Passo 0.

**Credenciais no `docker service inspect`:** `POSTGRES_PASSWORD` aparece em texto plano nas envs do serviço. Considerar Docker secrets. (Registro à parte: a senha do banco trafegou neste pedido de investigação — vale rotacioná-la.)

---

## Referências

| Item | Localização |
|---|---|
| Log da execução | `data/logs/etl-2026-07-25.log` |
| Falha das MVs | `src/rfb_cnpj_etl/db/postgres_builder.py:708-766` |
| Tratamento de FKs | `src/rfb_cnpj_etl/db/postgres_builder.py:284-330` |
| Avisos IBGE | `src/rfb_cnpj_etl/utils/ibge_lookup.py:165-204` |
| SQL da MV que falhou | `sql/materialized_views/08_mv_regime_tributario_cidade.sql` |
| Orquestração das etapas | `src/rfb_cnpj_etl/orchestrator.py:100-240` |
| Geração do `postgresql.conf` | `infra/postgres/generate-config.sh` |
| `shm_size` no compose | `infra/postgres/docker-compose.yml:29` |
| Tabela de recursos do container | `infra/postgres/docs/perfis.md:205-220` |
| Limitação conhecida de `shm_size` | `infra/postgres/docs/perfis.md:774-777` |
| Fórmula de coexistência | `infra/postgres/docs/perfis.md:555-591` |
