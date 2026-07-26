# Auditoria completa do pipeline — 26/07/2026

Registro datado da revisão de código, documentação e padrões executada em
26/07/2026. Quatro frentes: inventário funcional, padronização de idioma,
alinhamento de documentação e qualidade (bugs/performance).

## 1. Inventário funcional (baseline da auditoria)

- **14 comandos de CLI** (`get-availables`, `get-latest`, `get-urls`,
  `download`, `db init|load|patch|logged|pk|index|fk|search`,
  `db views create|refresh`, `complete`) — referência: [commands.md](commands.md)
- **11 etapas rastreáveis** com checkpoint/retomada — referência:
  [observabilidade.md](observabilidade.md)
- **18 variáveis de ambiente** lidas pelo código + as exclusivas do compose —
  referência canônica: [configuration.md](configuration.md)
- **14 tabelas** (+ `pipeline_stats` preservada e `busca_estabelecimento`
  build-and-swap), **19 índices básicos + 40 avançados**, **13 MVs** —
  referência: [database.md](database.md)
- **6 eventos de webhook**, estado JSON por período, dashboard com Basic Auth

Toda a documentação foi validada contra esse inventário; as divergências
encontradas (13 afirmações incorretas, 9 duplicações/contradições, dezenas de
omissões) foram corrigidas nos respectivos documentos.

## 2. Padronização de idioma

Convenção aplicada: **código, comentários, docstrings e chaves de JSON em
inglês; logs, erros ao usuário e interface do dashboard em português; domínio
de dados da RFB intocado** (`empresa`, `razao_social`, `idx_estab_*`, `mv_*`…).

- ~480 comentários/docstrings traduzidos; ~215 identificadores renomeados
- **Contrato público renomeado para inglês, sem retrocompatibilidade**
  (decisão do mantenedor — as features nunca haviam sido usadas em produção):
  - etapas: `download_arquivos`→`download`, `carga_dados`→`data_load`,
    `chaves_estrangeiras`→`foreign_keys` etc.
  - metadata: `arquivos_baixados`→`files_downloaded`, `tabela_atual`→`current_table`,
    `percentual`→`percent` etc.
  - environment/database: `so`→`os`, `porta`→`port`, `acessivel`→`reachable` etc.
  - JSONB de `pipeline_stats`: `{"tabela","linhas"}`→`{"table","rows"}`;
    detail de downloads: `filename`/`size_bytes`/`source_url`/`downloaded_at`
- Dashboard: código JS/Python em inglês; exibição 100% em português via mapas
  de tradução (status e nomes de etapa); 3 strings sem acento corrigidas
- Testes renomeados: `test_e2e_retomada.py`→`test_e2e_resume.py`,
  `test_observabilidade.py`→`test_observability.py`

> Estados `pipeline_state_*.json` gravados antes desta data usam as chaves
> antigas e não são reconhecidos como progresso. Se houver algum, apague-o ou
> rode com `--force`.

## 3. Bugs corrigidos

| # | Bug | Efeito antes |
|---|-----|--------------|
| 1 | `complete --parallel` era flag desligada por padrão (oposto de `db load`) | o comando principal carregava 220M de linhas com 1 worker |
| 2 | Lote de COPY que falhava era descartado com um log e **contado como inserido** | perda de dados silenciosa; agora: retry com reconexão → isolamento em dead-letter (`data/logs/dead_letter/`, CSV + `.meta`) → contagem correta → perda documentada no log, no metadata do estado e no dashboard. **A carga segue** (lote defeituoso da RFB não condena o fluxo); o reprocessamento — automático ou após correção manual — é feito com `db dead-letter --retry` |
| 3 | Arquivo que falhava em todas as tentativas de download não falhava a etapa | etapa `download` "concluía" com arquivo faltando; agora falha listando os arquivos |
| 4 | Erros de FK (≠ "já existe") eram engolidos no loop | o modo de falha do incidente de 07/2026; agora a etapa tenta todas e FALHA ao final listando as ausentes |
| 5 | Erros de índice coletados mas etapa nunca falhava | idem FKs |
| 6 | `ValueError` (validação, pasta ausente, mês inválido) terminava com **exit 0** | cron/CI viam sucesso em falha; agora exit 1 |
| 7 | `complete --download-dir X` baixava em `X/AAAA-MM` mas carregava de `X` | "PASTA NÃO ENCONTRADA" sempre; agora a carga lê da mesma subpasta |
| 8 | `pipeline_stats` procurava a tabela `municipio` (o nome real é `municipio_rfb`) | município nunca aparecia em `tables_populated` |
| 9 | HTTP 416 sem arquivo final mantinha o `.part` inválido e re-tentava em loop | agora o `.part` é descartado e o download recomeça limpo |
| 10 | `CREATE DATABASE` sem quoting do identificador | injeção via configuração; agora quotado |
| 11 | Barra de progresso da carga criada mas nunca animada; contadores só existiam no modo debug | agora a barra anima e os contadores valem nos dois modos |
| 12 | Limpezas: variável morta, busy-wait na fila (→ `put()` bloqueante), typo `get_availabes`→`get_availables`, `exceding`→`exceeding`, `DOWNLOAD_CHUNK_SIZE` 8_194→8_192, comentário de `BATCH_RATIO` desatualizado, `beautifulsoup4` removida do requirements (sem uso desde a migração para WebDAV) | — |

## 4. Performance (doutrina: o banco de carga não tem leitores)

O site aponta para o banco do mês anterior e só troca a conexão após o
pipeline concluir — portanto não há leitores a proteger e o objetivo é o menor
tempo total:

- **`CREATE INDEX` sem `CONCURRENTLY`** em todos os fluxos (1 varredura por
  índice em vez de 2)
- **Paralelismo por índice** em vez de por tabela: com lock SHARE
  (auto-compatível), vários índices da MESMA tabela constroem em paralelo —
  antes, `estabelecimento` (que concentra a maioria dos 40 avançados) era
  processada sequencialmente
- **`maintenance_work_mem`/`max_parallel_maintenance_workers` aplicados na
  conexão certa**: antes o `SET` ia para uma conexão que os workers não usavam
- Novas envs `INDEX_MAX_WORKERS` (4) e `INDEX_MAINTENANCE_WORK_MEM` (2GB);
  pico ≈ produto dos dois — reduzir em hosts de 8–16 GB
- **`synchronous_commit = off`** nas conexões de COPY (1 fsync a menos por
  lote; risco nulo — tabelas UNLOGGED e banco descartável até o fim)

## 5. Riscos identificados e apenas REPORTADOS (sem mudança de código)

1. **Estimativa de progresso** usa heurística fixa de 35 bytes/linha
   (`AVG_COMPRESSED_LINE_SIZE_BYTES`) — o percentual da carga é aproximado.
2. **Tipos assimétricos**: `estabelecimento_cnae_sec` usa `SMALLINT` para
   `cod_regiao_ibge`/`cod_estado_ibge`; `estabelecimento` usa `INTEGER`.
   Funciona (FK entre tipos inteiros é válida), mas mudar exigiria recarga.
3. **`COPY FREEZE`** economizaria o primeiro VACUUM das tabelas grandes, mas
   exige criar/truncar a tabela na mesma transação do COPY — refatoração de
   arquitetura do loader; não vale o risco agora.
4. **Alpine.js via CDN** no dashboard: sem internet no navegador do operador, a
   página degrada para o aviso + `state.json` cru. Alternativa: embutir o JS.
5. **`validate_zip_files` exige igualdade exata** do conjunto `{nome: tamanho}`
   — um arquivo extra na pasta reprova a validação (comportamento documentado).
6. **`--skip-download` implica `--skip-validation`** (documentado) — quem pula
   o download perde também a rede de segurança da validação.
7. **`sql/sitemap_indexes.sql`** ficou redundante (os 3 índices foram
   incorporados a `advanced_indexes.py`); mantido apenas como referência.

## Validação

- 4 suítes, **181 verificações, 0 falhas**: `test_observability.py` (93),
  `test_pipeline_stats.py` (24), `test_load_failures.py` (17, nova —
  retry/dead-letter, FKs, índices, exit code), `test_e2e_resume.py` (47)
- Compilação e import de todos os 23 módulos; `--help` de todos os subcomandos
- Varredura final: zero ocorrências das chaves/nomes antigos fora dos
  registros históricos datados
