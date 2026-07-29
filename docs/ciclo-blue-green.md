# Ciclo mensal blue/green

> Implementa os itens 24, 25 e 26 do roadmap 20 (arquitetura de busca).
> Código em `src/rfb_cnpj_etl/db/{schema_target,cycle,lock}.py`.

## O problema que ele resolve

Até este ciclo, a carga mensal começava por `db nuke` — `DROP` das tabelas — e
só depois baixava e carregava os dados da Receita. Entre o `DROP` e o fim da
carga, o site **não tinha base**. A janela medida foi de horas, e em 25/07/2026
uma execução do caminho antigo consumiu 6h43 sobre um banco que já estava
destruído: o `DROP` roda de novo sem reclamar, e ninguém percebe que o que se
está esperando não vai chegar.

O ciclo blue/green troca isso por: **carregar num schema novo enquanto o
anterior continua servindo**, e publicar por troca de `search_path` — que leva
menos de 5 segundos e é reversível na mesma ordem de grandeza.

```
dados_202607   ← o site lê daqui o mês inteiro (search_path)
dados_202608   ← a carga nova acontece aqui, sem ninguém olhando
                 ↓ publish (< 5 s)
dados_202608   ← o site passa a ler daqui
dados_202607   ← vira o N−1: fica 1 mês como saída de emergência
```

## Os schemas

| Schema | Papel |
|---|---|
| `dados_<load_id>` | uma geração de dados. `load_id` é `YYYYMM` (ex.: `202608`). |
| `ext` | extensões (`unaccent`, `pg_trgm`). Fora das gerações de propósito: `CREATE EXTENSION` é caro e não muda a cada mês. |
| `meta` | `meta.ciclos`, a tabela de etapas. Sobrevive a qualquer geração. |
| `public` | **não** entra no `search_path`. Um objeto esquecido em `public` seria encontrado por todas as gerações e mascararia a ausência dele na geração corrente. |

O `search_path` publicado é `"<schema>", ext` — sem `public`, deliberadamente.

## Os sete verbos

Todos aceitam `--db-name` (default: `POSTGRES_DBNAME`).

### `db bootstrap --load-id YYYYMM`

Prepara o terreno: cria `ext` e `meta`, cria `dados_<load_id>` e aplica o
**REVOKE de escrita no schema vigente**.

O `REVOKE` é a salvaguarda que impede o modo de falha mais caro: a carga nova
escrevendo por engano no schema que o site está lendo. Com ele, esse erro vira
`InsufficientPrivilege` na primeira instrução, e não corrupção silenciosa.

> **Se a role de carga for superusuário, o `REVOKE` não tem efeito** — o
> Postgres ignora privilégios para superusuários. A função avisa em voz alta
> quando detecta isso. Rodar a carga com uma role comum é o que faz a
> salvaguarda existir de fato.

### `db cycle --load-id YYYYMM`

`bootstrap` + a instrução de qual é o próximo passo. Ele não carrega: as etapas
de carga (`db load`, `db index`, `db views create`…) rodam em seguida com o
`search_path` já apontado para o schema novo.

### `db validate --load-id YYYYMM`

O portão de qualidade. **Não publica nada.** Reprova por qualquer uma de quatro
razões, cada uma cobrindo um modo de falha real:

| Checagem | O que ela pega |
|---|---|
| `estabelecimento` ≥ 60.000.000 linhas | arquivo-fonte truncado, que carrega **sem erro** |
| ao menos uma materialized view | o build das MVs falhou e ninguém viu |
| nenhuma MV vazia | a MV existe, tem 0 linhas, e a página que a consome fica em branco |
| gate de delta ≤ `MAX_DELTA_PCT` | bug de parsing, que produz linhas plausíveis e erradas |

O **gate de delta** é um `FULL OUTER JOIN` entre o schema novo e o vigente pela
coluna `row_hash` (um `md5(...)::uuid` calculado no CTAS da tabela de busca).
Ele mede quantas linhas mudaram entre as duas gerações. Uma carga mensal normal
muda poucos por cento; 25% ou mais não é a Receita, é a nossa leitura dela.

### `db publish --load-id YYYYMM`

Roda `validate` e, se passar, troca o `search_path` do banco
(`ALTER DATABASE ... SET search_path`). Menos de 5 segundos.

A validação roda **aqui**, e não antes: entre o fim da carga e o publish pode
ter passado tempo, e um portão que roda cedo demais aprova um estado que já não
existe mais.

`--pular-validacao` existe para depuração e **não deve ser usado em produção** —
o portão é exatamente o que impede publicar uma carga truncada.

### `db rollback [--para dados_YYYYMM]`

Volta o `search_path` para a geração anterior. Sem `--para`, vai para o N−1.
Medido em **menos de 60 segundos**, que é o critério de aceite do item 26.

> `rollback` e `gc` são os **únicos dois verbos que não pegam o lock**. Um
> rollback precisa acontecer justamente quando alguma coisa está travada;
> exigir o lock ali seria trancar a saída de emergência.

### `db gc [--apagar]`

Lista os schemas elegíveis a remoção — N−2 em diante. **Sem `--apagar` ele não
apaga nada**, só lista. O N−1 nunca entra na lista, em nenhuma circunstância:
ele é o destino do rollback.

### `db nuke --i-know-what-im-doing`

O antigo primeiro passo do ETL. **Fora do ciclo mensal desde o item 25.** Sem a
flag, ele recusa e sai com código 2.

## O lock compartilhado

`cnpj-pipeline`, `sitemap-service` e `search-indexer-service` disputam a **mesma
NVMe e o mesmo banco**. Rodar dois ao mesmo tempo não produz erro — produz uma
janela de carga que estoura sem que ninguém saiba por quê.

O `flock` fica em `/var/lib/bdh/pipeline.lock` (configurável por
`PIPELINE_LOCK_FILE`) e é advisory: só protege quem o pede. Os três serviços
pedem.

O diretório precisa existir e ser gravável pelo usuário que roda os três:

```bash
sudo mkdir -p /var/lib/bdh
sudo chown <usuario-do-pipeline> /var/lib/bdh
```

## Variáveis de ambiente

| Variável | Default | Para que serve |
|---|---|---|
| `MAX_DELTA_PCT` | `25` | teto do gate de delta, em por cento. Acima disso o `validate` reprova. |
| `PIPELINE_LOCK_FILE` | `/var/lib/bdh/pipeline.lock` | caminho do `flock` compartilhado entre os três serviços. Precisa ser **o mesmo** nos três. |

## A sequência de um mês

```bash
# 1. prepara o schema novo e revoga escrita no vigente
python etl.py db bootstrap --load-id 202608

# 2. baixa e carrega — o site continua servindo dados_202607 o tempo todo
python etl.py download --month 2026-08
python etl.py db load    --load-id 202608
python etl.py db index   --load-id 202608
python etl.py db views create --load-id 202608

# 3. portão de qualidade, sem publicar
python etl.py db validate --load-id 202608

# 4. publica — menos de 5 segundos
python etl.py db publish --load-id 202608

# 5. só no mês seguinte, e só depois de conferir: expurga o N−2
python etl.py db gc            # lista
python etl.py db gc --apagar   # apaga
```

Se algo der errado depois do passo 4:

```bash
python etl.py db rollback      # volta para dados_202607, em segundos
```

## O que o website precisa saber

Nada. A troca é no `search_path` do banco, e a aplicação usa
`'search_path' => 'public'` na conexão — que o `ALTER DATABASE` sobrescreve por
sessão nova. Conexões **já abertas** mantêm o `search_path` antigo até
reconectarem; com PgBouncer em modo transaction e `server_lifetime`, isso se
resolve sozinho em minutos.

## Testes

```bash
pytest tests/test_schema_blue_green.py   # 18 — unitários
pytest tests/test_ciclo_blue_green.py    # 10 — contra Postgres REAL
pytest tests/test_lock_e_nuke.py         #  5
```

`test_ciclo_blue_green.py` sobe um Postgres de verdade porque o que está sob
teste é `search_path`, `FULL OUTER JOIN` entre schemas, `ALTER DATABASE` e
privilégios — nada disso sobrevive a um mock.
