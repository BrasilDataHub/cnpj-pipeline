# Arquitetura PostgreSQL para 200M+ de Registros

## Análise Técnica e Proposta de Otimização para Base CNPJ

---

## Sumário Executivo

Este documento apresenta uma análise profunda do projeto ETL atual e propõe a **arquitetura mais eficiente** para um banco PostgreSQL que armazenará **+200 milhões de registros** de dados de CNPJ da Receita Federal do Brasil.

### Números Estimados da Base
| Tabela | Volume Estimado | Tamanho Aprox. |
|--------|-----------------|----------------|
| `estabelecimento` | ~58M registros | ~35 GB |
| `estabelecimento_cnae_sec` | ~180M registros | ~8 GB |
| `empresa` | ~58M registros | ~12 GB |
| `socio` | ~25M registros | ~6 GB |
| `simples` | ~40M registros | ~4 GB |
| **TOTAL** | ~361M registros | ~65 GB |

### Estratégia de CNPJ Completo

Para otimizar buscas exatas por CNPJ, o documento propõe a **computação da coluna `cnpj_completo` (14 caracteres) diretamente no Python durante o processo de carga**, evitando concatenação em runtime no banco de dados. Esta estratégia:

- **Reduz latência**: Busca exata por CNPJ completo sem concatenação (`WHERE cnpj_completo = '12345678000100'`)
- **Habilita índices únicos**: Permite constraint UNIQUE e lookup O(1) via índice BTREE ou HASH
- **Custo mínimo**: ~3.3 GB adicionais (812 MB para estabelecimento + 2.5 GB para cnae_sec)
- **Facilita JOINs**: JOIN entre tabelas por `cnpj_completo` é mais eficiente que por 3 colunas

Ver seção **3.6** para detalhes completos de implementação.

---

# 1. Perfil dos Filtros Reais

Os painéis fornecidos utilizam combinações repetitivas de filtros. Este perfil direciona todas as decisões a seguir:

- **Localização hierárquica**: `cod_regiao_ibge → cod_estado_ibge → cod_cidade_ibge`, além de filtros diretos por `cep` e `ddd`.
- **Natureza da atividade**: `cod_cnae_principal` (combinado com localização, situação cadastral, porte) e múltiplos CNAEs secundários.
- **Recorte temporal**: faixas em `data_inicio_atividade` e `data_situacao_cadastral`, principalmente ranges anuais e recentes (últimos 6/12/24 meses).
- **Atributos cadastrais**: `cod_situacao_cadastral`, `matriz_filial`, `cod_porte`, `cod_natureza_juridica`, flags de Simples/MEI.
- **Contato e presença digital**: filtros binários para "possui email", "possui telefone/celular" e deduplicação por contato.
- **Busca textual**: autocomplete + pesquisa `ILIKE` para nome fantasia, razão social e nome do sócio.
- **Relatórios pesados**: agregações por município/estado/região, CNAE, período de abertura e rankings top-N.

Todas as recomendações subsequentes são mapeadas explicitamente para essas combinações.

---

# 2. Análise do Estado Atual do Projeto ETL

## 2.1 Gargalos Identificados

### 🟡 Importante

5. **Transformação durante leitura** (`db_batch_producer.py:88-89`)
```python
transformed_rows = transform_batch(item, sanitizer_func)
```
**Problema**: Transformação síncrona no producer thread bloqueia leitura. Deveria ser feito no consumer.

### 🟢 Melhorias

6. **Índices criados sequencialmente** (`postgres_builder.py:203-209`)
```python
for i, (table_name, index) in enumerate(all_indexes, start=1):
    cur.execute(stmt)
```
**Melhoria**: Usar threads ou `CREATE INDEX CONCURRENTLY` em paralelo para índices independentes.

7. **VACUUM após patches incompleto** (`db_patch.py`)
O arquivo não executa VACUUM após os DELETEs massivos.

## 2.2 Bugs Prováveis

### Bug 1: Race condition na queue
```python
# db_batch_producer.py:92
while insertion_queue.full(): time.sleep(0.05)
insertion_queue.put(item)
```
**Problema**: Entre o check `full()` e o `put()`, outro thread pode inserir. Usar `put(item, timeout=X)` com try/except.

### Bug 2: Conexão não fechada em erro
```python
# postgres_loader.py:84-85
except Exception as fatal:
    print_log(f"[THREAD-{thread_id}] ERRO FATAL: {fatal}", level="error")
```
**Problema**: Conexão `conn` não é fechada em caso de exceção fatal.

### Bug 3: FK para cod_pais pode falhar
```python
# db_patch.py:73
cur.execute("UPDATE estabelecimento SET cod_pais = NULL WHERE cod_pais = '0';")
```
**Problema**: Se executado antes de inserir países extras (linhas 36-56), registros com `cod_pais='0'` podem ter FK violation.

## 2.3 Pontos de Melhoria

### Performance

5. **Desabilitar triggers/rules durante carga**
```sql
ALTER TABLE estabelecimento DISABLE TRIGGER ALL;
-- ... carga ...
ALTER TABLE estabelecimento ENABLE TRIGGER ALL;
```

6. **Pre-sort data** antes de inserir
Dados pré-ordenados por PK melhoram a criação de índices clustered.

## 2.4 Ajustes Estruturais Recomendados

### postgres_loader.py

```python
# Adicionar context manager para conexão
from contextlib import contextmanager

@contextmanager
def get_connection(config):
    conn = None
    try:
        conn = psycopg2.connect(**config)
        yield conn
    finally:
        if conn:
            conn.close()

# Usar no consumer
def consume_batches(...):
    with get_connection(postgres_config) as conn:
        # ... processamento
```

### postgres_builder.py

```python
# Criar índices em paralelo
from concurrent.futures import ThreadPoolExecutor

def create_indexes_parallel(self, max_workers=4):
    """Cria índices usando múltiplas conexões."""
    all_indexes = [...]  # coletar índices
    
    def create_single_index(index_info):
        table_name, index = index_info
        with self._connect() as conn:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(f'CREATE INDEX CONCURRENTLY ...')
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(create_single_index, all_indexes)
```

## 2.5 Otimizações no Fluxo Pós-Ingestão

### Sequência Otimizada

```sql
-- 1. Aplicar patches ANTES de criar constraints
-- (já implementado corretamente)

-- 2. Criar PK com USING INDEX para reutilizar
CREATE UNIQUE INDEX idx_empresa_pk ON empresa (cnpj_basico);
ALTER TABLE empresa ADD PRIMARY KEY USING INDEX idx_empresa_pk;

-- 3. CLUSTER tabelas por índice mais usado (OPCIONAL - muito lento)
-- CLUSTER estabelecimento USING idx_estab_estado_cnae;

-- 4. VACUUM FULL apenas se muitos deletes
-- Para carga inicial, VACUUM normal é suficiente
VACUUM (VERBOSE, ANALYZE) estabelecimento;

-- 5. Warm cache para índices críticos
SELECT pg_prewarm('idx_estab_estado_ibge');
SELECT pg_prewarm('idx_estab_cnae_principal');

-- 6. Reset statistics após carga
SELECT pg_stat_reset();
```

---

# 3. Estratégia ETL de Máxima Performance

## 3.1 Fluxo Otimizado de Ingestão

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PIPELINE ETL OTIMIZADO                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  FASE 1: PREPARAÇÃO (5 min)                                                 │
│  ├─ Criar banco com configurações otimizadas                                │
│  ├─ Criar UNLOGGED tables SEM constraints                                   │
│  ├─ Carregar tabelas auxiliares IBGE em memória (HashMap)                  │
│  └─ Pré-aquecer CSVs auxiliares                                            │
│                                                                              │
│  FASE 2: CARGA MASSIVA (2-3h)                                               │
│  ├─ COPY FROM STDIN com buffer de 256MB                                     │
│  ├─ Enriquecimento IBGE via HashMap O(1)                                   │
│  └─ Desabilitar fsync durante carga                                         │
│                                                                              │
│  FASE 3: PÓS-PROCESSAMENTO (30 min)                                         │
│  ├─ VACUUM FULL (recuperar espaço)                                         │
│  ├─ Criar índices (PARALLEL)                                               │
│  ├─ Adicionar PKs e FKs                                                    │
│  ├─ ANALYZE (estatísticas)                                                  │
│  └─ Converter para LOGGED tables (se necessário durabilidade)              │
│                                                                              │
│  FASE 4: MATERIALIZAÇÃO (20 min)                                            │
│  ├─ Criar Materialized Views de estatísticas                               │
│  └─ Criar índices nas MVs                                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 3.2 Configurações PostgreSQL para Carga Massiva

```sql
-- postgresql.conf durante ETL (ajustar para RAM disponível)
-- Para servidor com 32GB RAM:

-- Memória
shared_buffers = '8GB'              -- 25% da RAM
effective_cache_size = '24GB'       -- 75% da RAM
work_mem = '256MB'                  -- Alto para sorts durante index creation
maintenance_work_mem = '2GB'        -- Crítico para CREATE INDEX
temp_buffers = '256MB'

-- WAL (Write-Ahead Log) - DESABILITAR durante carga inicial
wal_level = 'minimal'               -- Mínimo necessário
max_wal_senders = 0                 -- Sem replicação durante carga
synchronous_commit = 'off'          -- PERIGOSO em produção!
wal_buffers = '64MB'
checkpoint_timeout = '30min'
checkpoint_completion_target = 0.9
max_wal_size = '16GB'

-- Autovacuum - DESABILITAR durante carga
autovacuum = off

-- Paralelismo para índices
max_parallel_workers = 8
max_parallel_maintenance_workers = 4
max_worker_processes = 12

-- Logging reduzido
log_statement = 'none'
log_min_duration_statement = -1
```

## 3.3 Script de Criação Otimizada de Tabelas

```sql
-- FASE 1: Criar tabelas UNLOGGED sem constraints

-- Tabela estabelecimento (maior volume)
CREATE UNLOGGED TABLE estabelecimento (
    cnpj_basico VARCHAR(8) NOT NULL,
    cnpj_ordem VARCHAR(4) NOT NULL,
    cnpj_dv VARCHAR(2) NOT NULL,
    cnpj_completo CHAR(14) NOT NULL,  -- CNPJ completo computado no Python durante carga
    matriz_filial CHAR(1) NOT NULL,
    nome_fantasia VARCHAR(60),
    cod_situacao_cadastral CHAR(2) NOT NULL,
    data_situacao_cadastral DATE,
    cod_motivo_situacao_cadastral CHAR(2),
    nome_cidade_exterior VARCHAR(60),
    cod_pais CHAR(3),
    data_inicio_atividade DATE NOT NULL,
    cod_cnae_principal VARCHAR(7) NOT NULL,
    cod_cnae_secundario TEXT,
    tipo_logradouro VARCHAR(20),
    logradouro VARCHAR(60),
    numero VARCHAR(6),
    complemento VARCHAR(200),
    bairro VARCHAR(60),
    cep CHAR(8),
    uf CHAR(2) NOT NULL,
    cod_municipio VARCHAR(7),
    ddd_telefone_1 CHAR(4),
    telefone_1 VARCHAR(10),
    ddd_telefone_2 CHAR(4),
    telefone_2 VARCHAR(10),
    ddd_fax CHAR(4),
    fax VARCHAR(10),
    email VARCHAR(120),
    situacao_especial VARCHAR(100),
    data_situacao_especial DATE,
    -- Campos desnormalizados IBGE
    cod_regiao_ibge SMALLINT,
    cod_estado_ibge SMALLINT,
    cod_cidade_ibge INTEGER
) WITH (
    fillfactor = 100,           -- 100% fill (dados não mudam)
    autovacuum_enabled = false  -- Desabilitado durante carga
);

-- Tabela empresa
CREATE UNLOGGED TABLE empresa (
    cnpj_basico VARCHAR(8) NOT NULL,
    razao_social VARCHAR(200),
    cod_natureza_juridica CHAR(4) NOT NULL,
    cod_qualificacao_responsavel CHAR(2) NOT NULL,
    capital_social NUMERIC(16,2) NOT NULL,
    cod_porte CHAR(2),
    ente_federativo_responsavel VARCHAR(100)
) WITH (fillfactor = 100);

-- Tabela CNAEs secundários (alto volume)
CREATE UNLOGGED TABLE estabelecimento_cnae_sec (
    cnpj_basico VARCHAR(8) NOT NULL,
    cnpj_ordem VARCHAR(4) NOT NULL,
    cnpj_dv VARCHAR(2) NOT NULL,
    cnpj_completo CHAR(14) NOT NULL,  -- CNPJ completo computado no Python durante carga
    cod_cnae VARCHAR(7) NOT NULL,
    cod_regiao_ibge SMALLINT,
    cod_estado_ibge SMALLINT,
    cod_cidade_ibge INTEGER,
    data_inicio_atividade DATE
) WITH (fillfactor = 100);
```

## 3.4 Fluxo Final Recomendado (checklist dos requisitos do ETL máximo desempenho)

| Requisito solicitado | Implementação proposta | Observações |
|----------------------|------------------------|-------------|
| **UNLOGGED tables na ingestão inicial** | Todas as tabelas massivas são criadas como `UNLOGGED`. | Reduz WAL; após carga converter para `LOGGED`. |
| **Desabilitar FKs/índices até o fim** | Carregar apenas heap + PK desativada; índices/FKs criados após `VACUUM`. | Evita validações linha a linha. |
| **Criar índices somente depois do COPY** | Scripts separados (`sql/indices_otimizados.sql`). | `maintenance_work_mem` alto + paralelismo. |
| **Ingestão paralela (multi-workers)** | `WORKER_THREADS = cpu_count-1`, 4 producers (I/O) + N consumers (COPY). | `COPY FREEZE` para otimização de carga. |
| **Pré-carregar CSVs IBGE em memória** | `IBGE_LOOKUP` inicializa arrays densos e mantém hash maps (`__slots__`). | Permite O(1) para região/estado/cidade. |
| **Evitar JOINs durante ingestão** | Lookup resolve códigos IBGE e flags diretamente, gravando já desnormalizado. | Nenhum SELECT no banco durante inserção. |
| **Mapear região/estado/cidade via hash maps** | `OptimizedIBGELookup` usa arrays e dicionários normalizados em RAM. | Permite enriquecimento eficiente durante transformação. |
| **VACUUM/ANALYZE pós-carga** | Sequência obrigatória (ver 3.5) + `pg_prewarm` índices críticos. | Reativa autovacuum depois. |

### Fluxo em alto nível

1. **Pré-carga**
   - Iniciar servidor com parâmetros específicos (`shared_buffers`, `work_mem`, `wal_level=minimal`, `autovacuum=off`).
   - Criar schema e tabelas auxiliares UNLOGGED sem constraints.
   - Carregar CSVs IBGE/IBGE hash maps para memória (processo Python) e pré-aquecer MVs auxiliares.
2. **Ingestão massiva**
   - Descompactar ZIPs em streaming, produzindo batches de até 100k linhas para estabelecimento (500k demais tabelas).
   - Transformar e enriquecer dados ainda na camada Python, atribuindo `cod_regiao/estado/cidade`, `cnpj_completo` (concatenação de `cnpj_basico + cnpj_ordem + cnpj_dv`) e datas antes do COPY.
3. **Pós-processamento imediato**
   - Rodar patches/deduplicações enquanto tabelas estão UNLOGGED.
   - Executar `VACUUM (ANALYZE, VERBOSE)`, seguidos da criação paralela dos índices e PKs (`USING INDEX`).
   - Converter para `LOGGED`, reabilitar FK/constraints, reativar `autovacuum`.
4. **Materialização e cache**
   - Criar/atualizar as Materialized Views e respectivos índices.
   - Rodar `pg_prewarm` nos índices mais usados (CNAE+Estado, Cidade, BRIN datas) e executar `ANALYZE` final.

## 3.6 Estratégia de Computação do CNPJ Completo Durante a Carga

### Objetivo
Computar a coluna `cnpj_completo` (14 caracteres) diretamente no Python durante o processo de transformação, evitando concatenação em runtime no banco de dados e habilitando buscas exatas ultra-rápidas via índice.

### Formato do CNPJ Completo
- **Composição**: `cnpj_basico` (8 chars) + `cnpj_ordem` (4 chars) + `cnpj_dv` (2 chars) = **14 caracteres**
- **Exemplo**: `12345678` + `0001` + `00` = `12345678000100`
- **Tipo**: `CHAR(14)` (tamanho fixo para otimização de índice)

### Tabelas que Devem Incluir `cnpj_completo`
1. **`estabelecimento`** (~58M registros) - **Obrigatório**
2. **`estabelecimento_cnae_sec`** (~180M registros) - **Obrigatório**
3. **`socio`** (~25M registros) - Opcional (já possui `cnpj_cpf_socio`)

### Implementação no Python

A computação deve ocorrer na função `transform_batch` em `utils/db_transformers.py`, **antes** do COPY para o banco:

```python
# utils/db_transformers.py

def compute_cnpj_completo(rows: List[List], columns: List[str]) -> List[List]:
    """
    Computa cnpj_completo concatenando cnpj_basico + cnpj_ordem + cnpj_dv.
    A coluna cnpj_completo deve estar presente em columns e será preenchida.
    """
    # Encontrar índices das colunas CNPJ
    idx_basico = columns.index('cnpj_basico')
    idx_ordem = columns.index('cnpj_ordem')
    idx_dv = columns.index('cnpj_dv')
    idx_completo = columns.index('cnpj_completo')
    
    new_rows = []
    for row in rows:
        row = list(row)
        # Garantir que os valores são strings e preencher com zeros à esquerda se necessário
        basico = str(row[idx_basico] or '').zfill(8)
        ordem = str(row[idx_ordem] or '').zfill(4)
        dv = str(row[idx_dv] or '').zfill(2)
        
        # Concatenar e garantir exatamente 14 caracteres
        cnpj_completo = (basico + ordem + dv)[:14].ljust(14, '0')
        row[idx_completo] = cnpj_completo
        new_rows.append(row)
    
    return new_rows


def transform_batch(item: dict, sanitizer_func: Callable) -> List:
    """
    Aplica todas as transformações necessárias a um lote de dados.
    """
    table = item["table"]
    columns = item["columns"]
    rows = item["rows"]

    rows = sanitizer_func(rows)

    if table == "empresa":
        rows = normalize_numeric_br(rows, columns, ["capital_social"])

    elif table == "estabelecimento":
        rows = normalize_dates(rows, columns, [
            "data_situacao_cadastral", "data_inicio_atividade", "data_situacao_especial"
        ])
        rows = IBGE_LOOKUP.append_ibge_to_estabelecimentos(rows, columns)
        # Computar CNPJ completo ANTES do COPY
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "estabelecimento_cnae_sec":
        # Computar CNPJ completo para CNAEs secundários também
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "simples":
        rows = normalize_dates(
            rows, columns,
            ["data_opcao_simples", "data_exclusao_simples", "data_opcao_mei", "data_exclusao_mei"]
        )

    elif table == "socio":
        rows = normalize_dates(rows, columns, ["data_entrada_sociedade"])

    return rows
```

### Atualização do Schema

O `schema.py` deve incluir `cnpj_completo` nas definições das tabelas:

```python
# db/schema.py

'estabelecimento': {
    'columns': [
        ('cnpj_basico', 'VARCHAR(8) NOT NULL'),
        ('cnpj_ordem', 'VARCHAR(4) NOT NULL'),
        ('cnpj_dv', 'VARCHAR(2) NOT NULL'),
        ('cnpj_completo', 'CHAR(14) NOT NULL'),  # ← Adicionar esta linha
        # ... demais colunas
    ]
},

'estabelecimento_cnae_sec': {
    'columns': [
        ('cnpj_basico', 'VARCHAR(8) NOT NULL'),
        ('cnpj_ordem', 'VARCHAR(4) NOT NULL'),
        ('cnpj_dv', 'VARCHAR(2) NOT NULL'),
        ('cnpj_completo', 'CHAR(14) NOT NULL'),  # ← Adicionar esta linha
        ('cod_cnae', 'VARCHAR(7) NOT NULL'),
        # ... demais colunas
    ]
}
```

### Ordem de Processamento

1. **Leitura do arquivo**: Linhas brutas com `cnpj_basico`, `cnpj_ordem`, `cnpj_dv` separados
2. **Sanitização**: Limpeza de caracteres inválidos
3. **Transformação**: 
   - Normalização de datas
   - Enriquecimento IBGE
   - **Computação de `cnpj_completo`** ← **AQUI**
4. **COPY para banco**: Dados já incluem `cnpj_completo` preenchido

### Benefícios

- **Performance**: Busca exata por CNPJ completo sem concatenação em runtime (`WHERE cnpj_completo = '12345678000100'`)
- **Índice único**: Permite constraint UNIQUE e lookup O(1) via índice BTREE ou HASH
- **Custo mínimo**: ~812 MB para estabelecimento + ~2.5 GB para cnae_sec (total ~3.3 GB)
- **Facilita JOINs**: JOIN entre `estabelecimento` e `estabelecimento_cnae_sec` por `cnpj_completo` é mais eficiente que por 3 colunas

### Validação

Após a carga, validar que todos os registros têm `cnpj_completo` preenchido:

```sql
-- Verificar que não há NULLs
SELECT COUNT(*) FROM estabelecimento WHERE cnpj_completo IS NULL;
-- Esperado: 0

-- Verificar formato (deve ter exatamente 14 caracteres)
SELECT COUNT(*) FROM estabelecimento WHERE LENGTH(cnpj_completo) != 14;
-- Esperado: 0

-- Validar consistência com componentes
SELECT COUNT(*) FROM estabelecimento 
WHERE cnpj_completo != (cnpj_basico || cnpj_ordem || cnpj_dv);
-- Esperado: 0 (ou próximo de 0, considerando padding)
```

## 3.5 Sequência de Comandos Pós-Carga

```sql
-- FASE 3: Após carga completa

-- Pré-requisito: toda carga foi feita com COPY ... FREEZE e triggers/FKs desabilitados.

-- 1. VACUUM FULL para recuperar espaço e otimizar storage
VACUUM FULL estabelecimento;
VACUUM FULL empresa;
VACUUM FULL estabelecimento_cnae_sec;
VACUUM FULL socio;
VACUUM FULL simples;

-- 2. Criar índices em PARALELO (usar max_parallel_maintenance_workers)
SET max_parallel_maintenance_workers = 4;
SET maintenance_work_mem = '2GB';

-- Índices criados na seção 5

-- 3. Adicionar PKs (cria índice unique automaticamente)
ALTER TABLE empresa ADD PRIMARY KEY (cnpj_basico);
ALTER TABLE estabelecimento ADD PRIMARY KEY (cnpj_basico, cnpj_ordem, cnpj_dv);

-- 4. ANALYZE para estatísticas precisas
ANALYZE estabelecimento;
ANALYZE empresa;
ANALYZE estabelecimento_cnae_sec;
ANALYZE socio;
ANALYZE simples;

-- 5. (Opcional) Converter para LOGGED se precisar de durabilidade
ALTER TABLE estabelecimento SET LOGGED;
ALTER TABLE empresa SET LOGGED;
-- ... outras tabelas

-- 6. Reabilitar autovacuum
ALTER TABLE estabelecimento SET (autovacuum_enabled = true);
```

---

# 4. Desnormalização Estratégica

## 4.1 Colunas que DEVEM Estar no Registro Principal

### Já implementadas (correto) ✅

```python
# schema.py - já presente
'estabelecimento': {
    'columns': [
        # ...
        ('cod_regiao_ibge', 'INTEGER'),      # ✅ Desnormalizado
        ('cod_estado_ibge', 'INTEGER'),       # ✅ Desnormalizado  
        ('cod_cidade_ibge', 'INTEGER'),       # ✅ Desnormalizado
        ('cod_cnae_principal', 'VARCHAR(7)'), # ✅ Permanece no registro
        ('cnpj_completo', 'CHAR(14)'),        # ✅ Computado no Python durante carga
    ]
}
```

### Reforços recomendados 📋

```sql
-- Campos duplicados que evitam JOINs em filtros reais
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS cod_porte CHAR(2);
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS cod_natureza_juridica CHAR(4);
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS tem_email BOOLEAN GENERATED ALWAYS AS (email IS NOT NULL AND email <> '') STORED;
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS tem_telefone BOOLEAN GENERATED ALWAYS AS (telefone_1 IS NOT NULL AND telefone_1 <> '') STORED;
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS tem_contato BOOLEAN GENERATED ALWAYS AS (
    (email IS NOT NULL AND email <> '') OR (telefone_1 IS NOT NULL AND telefone_1 <> '')
) STORED;

-- Replicações necessárias para índices de CNAE secundário
ALTER TABLE estabelecimento_cnae_sec ADD COLUMN IF NOT EXISTS cod_regiao_ibge SMALLINT;
ALTER TABLE estabelecimento_cnae_sec ADD COLUMN IF NOT EXISTS cod_estado_ibge SMALLINT;
ALTER TABLE estabelecimento_cnae_sec ADD COLUMN IF NOT EXISTS cod_cidade_ibge INTEGER;
ALTER TABLE estabelecimento_cnae_sec ADD COLUMN IF NOT EXISTS data_inicio_atividade DATE;

-- CNPJ completo (computado no Python durante carga, não via ALTER TABLE)
-- A coluna já deve estar presente no schema e ser populada durante o ETL
-- Ver seção 3.6 para detalhes de implementação

-- Na tabela empresa, manter nomes para uso textual (custo baixo)
ALTER TABLE empresa ADD COLUMN IF NOT EXISTS nome_natureza VARCHAR(200);
ALTER TABLE empresa ADD COLUMN IF NOT EXISTS nome_porte VARCHAR(50);
```

## 4.2 Análise de Custo-Benefício

| Coluna Desnormalizada | Espaço Extra | Economia de JOIN | Recomendação |
|----------------------|--------------|------------------|--------------|
| `cod_regiao_ibge` (estab + cnae_sec) | ~116 MB + ~360 MB | Altíssima | ✅ **Obrigatório** |
| `cod_estado_ibge` | ~116 MB + ~360 MB | Altíssima | ✅ **Obrigatório** |
| `cod_cidade_ibge` | ~232 MB + ~700 MB | Alta | ✅ **Obrigatório** |
| `cod_porte` no estabelecimento | ~120 MB | Alta (filtro cruzado com CNAE) | ✅ **Adicionar** |
| `cod_natureza_juridica` no estabelecimento | ~232 MB | Alta | ✅ **Adicionar** |
| Flags `tem_email`/`tem_telefone` | ~40 MB | Alta (substitui `OR IS NOT NULL`) | ✅ **Adicionar** |
| `cnpj_completo` (estab + cnae_sec) | ~812 MB + ~2.5 GB | Altíssima (busca exata sem concatenação) | ✅ **Obrigatório** |
| `nome_cidade` | ~3.5 GB | Média (display/apresentação) | ⚠️ Preferir MV/lookup |
| `sigla_uf_ibge` | ~116 MB | Baixa (campo `uf` já existe) | ❌ Não necessário |
| `nome_natureza`/`nome_porte` na empresa | ~700 MB | Média (export/relatório) | ⚠️ Manter em empresa/MVs, não em estab |

### Recomendação Final de Desnormalização

1. **Estabelecimento** deve carregar códigos IBGE, `cod_porte`, `cod_natureza_juridica`, flags de contato, CNAE principal (já existe) e **`cnpj_completo`** (computado no Python durante carga). `nome_estado/nome_cidade` ficam fora para evitar gasto de 6GB; usar `mv_stats_*` ou lookup em cache para apresentação.
2. **Estabelecimento_cnae_sec** deve receber os códigos de localização, `data_inicio_atividade` e **`cnpj_completo`** ainda no ETL, viabilizando filtros reais sem JOINs custosos e buscas diretas por CNPJ completo.
3. **Empresa** mantém campos de display (`nome_natureza`, `nome_porte`) porque o volume é bem menor (58M) e evita join com tabelas minúsculas durante exportações.

## 4.3 Quando Duplicar Dados é Benefício Real

```sql
-- CASO 1: Contagem pré-computada de CNAEs secundários
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS 
    qtd_cnaes_secundarios SMALLINT DEFAULT 0;

-- Atualizar após carga
UPDATE estabelecimento e
SET qtd_cnaes_secundarios = (
    SELECT COUNT(*) 
    FROM estabelecimento_cnae_sec sec 
    WHERE sec.cnpj_basico = e.cnpj_basico 
      AND sec.cnpj_ordem = e.cnpj_ordem 
      AND sec.cnpj_dv = e.cnpj_dv
);

-- CASO 2: Flag de "tem contato" (evita OR complexo)
ALTER TABLE estabelecimento ADD COLUMN IF NOT EXISTS 
    tem_contato BOOLEAN GENERATED ALWAYS AS (
        (email IS NOT NULL AND email <> '') OR
        (telefone_1 IS NOT NULL AND telefone_1 <> '') OR
        (telefone_2 IS NOT NULL AND telefone_2 <> '')
    ) STORED;

CREATE INDEX CONCURRENTLY idx_estab_tem_contato 
    ON estabelecimento (tem_contato) WHERE tem_contato = TRUE;

-- CASO 3: CNPJ completo (computado no Python durante carga)
-- IMPORTANTE: Esta coluna NÃO deve ser criada via GENERATED ALWAYS AS.
-- Ela deve ser computada no Python durante o processo de transformação (ver seção 3.6)
-- e inserida diretamente no banco durante o COPY.

-- A coluna já deve estar no schema:
-- cnpj_completo CHAR(14) NOT NULL

-- Índices para busca exata por CNPJ completo (criados após carga)
CREATE UNIQUE INDEX CONCURRENTLY idx_estab_cnpj_completo 
    ON estabelecimento (cnpj_completo);

CREATE INDEX CONCURRENTLY idx_cnae_sec_cnpj_completo 
    ON estabelecimento_cnae_sec (cnpj_completo);
```

---

# 5. Índices Ideais para Todos os Filtros

## 5.1 Índices Obrigatórios por Categoria

### Localização (Alta Prioridade)

```sql
-- Índices simples para filtros de localização
CREATE INDEX CONCURRENTLY idx_estab_cidade_ibge 
    ON estabelecimento (cod_cidade_ibge);
    
CREATE INDEX CONCURRENTLY idx_estab_estado_ibge 
    ON estabelecimento (cod_estado_ibge);
    
CREATE INDEX CONCURRENTLY idx_estab_regiao_ibge 
    ON estabelecimento (cod_regiao_ibge);

-- CEP (filtro comum)
CREATE INDEX CONCURRENTLY idx_estab_cep 
    ON estabelecimento (cep);

-- DDD (extraído do telefone)
CREATE INDEX CONCURRENTLY idx_estab_ddd 
    ON estabelecimento (ddd_telefone_1);

-- Índice composto para filtros região+estado (otimiza consultas hierárquicas)
CREATE INDEX CONCURRENTLY idx_estab_regiao_estado 
    ON estabelecimento (cod_regiao_ibge, cod_estado_ibge);
```

### CNAEs (Crítico para Negócio)

```sql
-- CNAE principal (filtro mais usado)
CREATE INDEX CONCURRENTLY idx_estab_cnae_principal 
    ON estabelecimento (cod_cnae_principal);

-- CNAE + Estado (consulta combinada frequente)
CREATE INDEX CONCURRENTLY idx_estab_cnae_estado 
    ON estabelecimento (cod_cnae_principal, cod_estado_ibge);

-- CNAE + Cidade (para consultas locais)
CREATE INDEX CONCURRENTLY idx_estab_cnae_cidade 
    ON estabelecimento (cod_cnae_principal, cod_cidade_ibge);

-- CNAEs secundários
CREATE INDEX CONCURRENTLY idx_cnae_sec_cnae 
    ON estabelecimento_cnae_sec (cod_cnae);

-- CNAE secundário + estabelecimento (para JOIN)
CREATE INDEX CONCURRENTLY idx_cnae_sec_estab 
    ON estabelecimento_cnae_sec (cnpj_basico, cnpj_ordem, cnpj_dv);

-- CNAE secundário + Estado (via subquery)
CREATE INDEX CONCURRENTLY idx_cnae_sec_cnae_covering 
    ON estabelecimento_cnae_sec (cod_cnae) 
    INCLUDE (cnpj_basico, cnpj_ordem, cnpj_dv);

-- CNAE secundário + localização (requer colunas desnormalizadas na carga)
CREATE INDEX CONCURRENTLY idx_cnae_sec_cnae_estado 
    ON estabelecimento_cnae_sec (cod_cnae, cod_estado_ibge);

CREATE INDEX CONCURRENTLY idx_cnae_sec_cnae_cidade 
    ON estabelecimento_cnae_sec (cod_cnae, cod_cidade_ibge);

CREATE INDEX CONCURRENTLY idx_cnae_sec_cnae_regiao 
    ON estabelecimento_cnae_sec (cod_cnae, cod_regiao_ibge);
```

### Datas (Filtro por Período)

```sql
-- Data de início de atividade (filtro de período)
-- BRIN é excelente para datas naturalmente ordenadas
CREATE INDEX CONCURRENTLY idx_estab_data_inicio_brin 
    ON estabelecimento USING BRIN (data_inicio_atividade)
    WITH (pages_per_range = 32);

-- BTREE para consultas pontuais (redundante mas útil)
CREATE INDEX CONCURRENTLY idx_estab_data_inicio 
    ON estabelecimento (data_inicio_atividade);

-- Data de situação cadastral
CREATE INDEX CONCURRENTLY idx_estab_data_situacao 
    ON estabelecimento (data_situacao_cadastral);
```

### Situação Cadastral / Natureza / Porte / Matriz-Filial

```sql
-- Situação cadastral (02=Ativa é 60% dos dados - considerar parcial)
CREATE INDEX CONCURRENTLY idx_estab_situacao 
    ON estabelecimento (cod_situacao_cadastral);

-- Índice parcial para ATIVAS (otimiza consultas mais comuns)
CREATE INDEX CONCURRENTLY idx_estab_ativas 
    ON estabelecimento (cod_situacao_cadastral)
    WHERE cod_situacao_cadastral = '02';

-- Matriz/Filial
CREATE INDEX CONCURRENTLY idx_estab_matriz_filial 
    ON estabelecimento (matriz_filial);

-- Porte (na tabela empresa)
CREATE INDEX CONCURRENTLY idx_empresa_porte 
    ON empresa (cod_porte);

-- Natureza jurídica (na tabela empresa)
CREATE INDEX CONCURRENTLY idx_empresa_natureza 
    ON empresa (cod_natureza_juridica);

-- Capital social (range queries)
CREATE INDEX CONCURRENTLY idx_empresa_capital 
    ON empresa (capital_social);
```

### Busca Textual (Nome Fantasia / Razão Social / Nome do Sócio)

```sql
-- Habilitar extensão pg_trgm
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN trigram para ILIKE '%termo%' em nome_fantasia
CREATE INDEX CONCURRENTLY idx_estab_nome_fantasia_trgm 
    ON estabelecimento USING GIN (nome_fantasia gin_trgm_ops);

-- GIN trigram para razão social
CREATE INDEX CONCURRENTLY idx_empresa_razao_social_trgm 
    ON empresa USING GIN (razao_social gin_trgm_ops);

-- Índice BTREE para prefixo (LIKE 'termo%' - mais rápido)
CREATE INDEX CONCURRENTLY idx_estab_nome_fantasia_prefix 
    ON estabelecimento (nome_fantasia varchar_pattern_ops);

CREATE INDEX CONCURRENTLY idx_empresa_razao_social_prefix 
    ON empresa (razao_social varchar_pattern_ops);
```

### Contatos (Email / Telefone / Celular)

```sql
-- Email (para filtro "empresas com email")
CREATE INDEX CONCURRENTLY idx_estab_email 
    ON estabelecimento (email) 
    WHERE email IS NOT NULL AND email != '';

-- Índice parcial + unique para deduplicação e normalização
CREATE UNIQUE INDEX CONCURRENTLY idx_estab_email_unique
    ON estabelecimento (lower(email))
    WHERE email IS NOT NULL AND email <> '';

-- Telefone (parcial para não-nulos)
CREATE INDEX CONCURRENTLY idx_estab_telefone 
    ON estabelecimento (telefone_1) 
    WHERE telefone_1 IS NOT NULL AND telefone_1 != '';

-- Celular (identificado por DDD ou padrão 9XXXX)
-- Índice para identificar celulares
CREATE INDEX CONCURRENTLY idx_estab_celular 
    ON estabelecimento (telefone_1) 
    WHERE telefone_1 LIKE '9%' AND LENGTH(telefone_1) >= 9;

-- Hash index para deduplicação por email (lookup exato)
CREATE INDEX CONCURRENTLY idx_estab_email_hash 
    ON estabelecimento USING HASH (email);

-- Hash para deduplicação por telefone
CREATE INDEX CONCURRENTLY idx_estab_telefone_hash 
    ON estabelecimento USING HASH (telefone_1);

-- Parcial para celulares válidos
CREATE INDEX CONCURRENTLY idx_estab_celular_unique
    ON estabelecimento (telefone_1)
    WHERE telefone_1 ~ '^[5-9][0-9]{8,}$';
```

### CNPJ Completo (Busca Exata por CNPJ)

```sql
-- CNPJ completo em estabelecimento (busca exata sem concatenação)
CREATE UNIQUE INDEX CONCURRENTLY idx_estab_cnpj_completo 
    ON estabelecimento (cnpj_completo);

-- CNPJ completo em CNAE secundário (para JOINs e filtros)
CREATE INDEX CONCURRENTLY idx_cnae_sec_cnpj_completo 
    ON estabelecimento_cnae_sec (cnpj_completo);

-- Índice HASH para lookup ultra-rápido (opcional, para consultas pontuais)
CREATE INDEX CONCURRENTLY idx_estab_cnpj_completo_hash 
    ON estabelecimento USING HASH (cnpj_completo);
```

### Sócios

```sql
-- CPF/CNPJ do sócio (consultas de vinculação)
CREATE INDEX CONCURRENTLY idx_socio_cpf_cnpj 
    ON socio (cnpj_cpf_socio);

-- Nome do sócio (busca textual com GIN trigram - suporta ILIKE '%termo%')
CREATE INDEX CONCURRENTLY idx_socio_nome_trgm 
    ON socio USING GIN (nome_socio gin_trgm_ops);

-- Índice BTREE para prefixo (LIKE 'termo%' - mais rápido para autocomplete)
CREATE INDEX CONCURRENTLY idx_socio_nome_prefix 
    ON socio (nome_socio varchar_pattern_ops);

-- Empresa do sócio (JOIN)
CREATE INDEX CONCURRENTLY idx_socio_empresa 
    ON socio (cnpj_basico);
```

## 5.2 Índices Compostos para Consultas Reais

Com base nos filtros das imagens, estas são as **combinações mais comuns**:

```sql
-- Combo: Estado + CNAE + Situação + Porte (consulta típica de prospecção)
CREATE INDEX CONCURRENTLY idx_estab_prospeccao 
    ON estabelecimento (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- Combo: Cidade + CNAE + Matriz (consulta local)
CREATE INDEX CONCURRENTLY idx_estab_local_cnae 
    ON estabelecimento (cod_cidade_ibge, cod_cnae_principal, matriz_filial);

-- Combo: Período + Estado (novos estabelecimentos)
CREATE INDEX CONCURRENTLY idx_estab_novos_estado 
    ON estabelecimento (data_inicio_atividade, cod_estado_ibge);

-- Combo: CNAE + Situação + Com Email (lead generation)
CREATE INDEX CONCURRENTLY idx_estab_leads_email 
    ON estabelecimento (cod_cnae_principal, cod_situacao_cadastral)
    WHERE email IS NOT NULL AND email != '';

-- Combo: Estado + CNAE + Data (análise temporal)
CREATE INDEX CONCURRENTLY idx_estab_temporal 
    ON estabelecimento (cod_estado_ibge, cod_cnae_principal, data_inicio_atividade);
```

## 5.3 Resumo de Índices (Total: ~35)

| Categoria | Quantidade | Espaço Estimado |
|-----------|------------|-----------------|
| Localização | 6 | ~3 GB |
| CNAEs | 6 | ~4 GB |
| Datas | 3 | ~1 GB |
| Situação/Tipo | 5 | ~2 GB |
| Texto (GIN) | 5 | ~9 GB |
| Contatos | 5 | ~2 GB |
| CNPJ Completo | 3 | ~1 GB |
| Compostos | 6 | ~4 GB |
| **TOTAL** | **39** | **~26 GB** |

## 5.4 Cobertura explícita dos filtros reais

| Filtro real | Índice correspondente | Observações |
|-------------|----------------------|-------------|
| Região → Estado → Cidade | `idx_estab_regiao_ibge`, `idx_estab_estado_ibge`, `idx_estab_cidade_ibge`, `idx_estab_regiao_estado` | Hierarquia garante que qualquer nível execute via Index Scan. |
| CEP ou faixa de CEP | `idx_estab_cep` | Pode ser combinado com filtro de situação usando `BitmapAnd`. |
| DDD + CNAE | `idx_estab_ddd` + `idx_estab_cnae_principal` ou `idx_estab_cnae_estado` | Considerar `BitmapAnd`; DDD usa pouca cardinalidade. |
| CNAE principal + Estado + Situação | `idx_estab_prospeccao` | Cobertura direta dos principais filtros do painel. |
| CNAE principal + Cidade + Matriz/Filial | `idx_estab_local_cnae` | Otimiza funil de matrizes ativo por cidade. |
| CNAE secundário + Estado/Cidade/Região | `idx_cnae_sec_cnae_estado`, `idx_cnae_sec_cnae_cidade`, `idx_cnae_sec_cnae_regiao` | Viabiliza o uso real dos filtros de secundários sem JOIN custoso. |
| Data de abertura (range) + Estado | `idx_estab_novos_estado` + BRIN `idx_estab_data_inicio_brin` | Planner escolhe BRIN para range amplo e BTREE para intervalos curtos. |
| Situação cadastral + porte | `idx_estab_situacao`, `idx_empresa_porte` | Usadas em relatórios de porte por status. |
| Possui contato | `idx_estab_tem_contato` | Campo desnormalizado reduz OR/IS NOT NULL. |
| Nome fantasia / razão social (texto) | `idx_estab_nome_fantasia_trgm`, `idx_empresa_razao_social_trgm` | Cobrem autocomplete e busca textual. |
| Nome do sócio (texto) | `idx_socio_nome_trgm`, `idx_socio_nome_prefix` | Busca textual e autocomplete para sócios. |
| Leads com email | `idx_estab_leads_email` | Filtra e ordena com custo mínimo. |
| Busca exata por CNPJ completo | `idx_estab_cnpj_completo`, `idx_cnae_sec_cnpj_completo` | Lookup O(1) sem concatenação em runtime. |

---

# 6. Materialized Views

## 6.1 MVs Essenciais para Estatísticas

### MV: Estatísticas por Município

```sql
CREATE MATERIALIZED VIEW mv_stats_municipio AS
SELECT 
    e.cod_cidade_ibge,
    c.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    e.cod_regiao_ibge,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    MIN(e.data_inicio_atividade) AS primeira_abertura,
    MAX(e.data_inicio_atividade) AS ultima_abertura,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN ibge_cidade c ON e.cod_cidade_ibge = c.cod_cidade_ibge
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY 
    e.cod_cidade_ibge, c.nome_cidade, 
    e.cod_estado_ibge, est.sigla_uf, 
    e.cod_regiao_ibge;

CREATE UNIQUE INDEX idx_mv_stats_municipio_pk 
    ON mv_stats_municipio (cod_cidade_ibge);
CREATE INDEX idx_mv_stats_municipio_estado 
    ON mv_stats_municipio (cod_estado_ibge);
```

### MV: Estatísticas por Estado

```sql
CREATE MATERIALIZED VIEW mv_stats_estado AS
SELECT 
    e.cod_estado_ibge,
    est.sigla_uf,
    est.nome_estado,
    e.cod_regiao_ibge,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone,
    ROUND(AVG(emp.capital_social), 2) AS capital_social_medio,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
LEFT JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
GROUP BY 
    e.cod_estado_ibge, est.sigla_uf, est.nome_estado, e.cod_regiao_ibge;

CREATE UNIQUE INDEX idx_mv_stats_estado_pk 
    ON mv_stats_estado (cod_estado_ibge);
```

### MV: Estatísticas por CNAE

```sql
CREATE MATERIALIZED VIEW mv_stats_cnae AS
SELECT 
    e.cod_cnae_principal,
    c.nome_cnae,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(DISTINCT e.cod_estado_ibge) AS estados_presentes,
    COUNT(DISTINCT e.cod_cidade_ibge) AS cidades_presentes,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    ROUND(AVG(emp.capital_social), 2) AS capital_social_medio,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '6 months') AS novos_6meses,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '1 year') AS novos_1ano,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '2 years') AS novos_2anos,
    COUNT(*) FILTER (WHERE e.data_inicio_atividade >= CURRENT_DATE - INTERVAL '4 years') AS novos_4anos
FROM estabelecimento e
LEFT JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
LEFT JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
GROUP BY e.cod_cnae_principal, c.nome_cnae;

CREATE UNIQUE INDEX idx_mv_stats_cnae_pk 
    ON mv_stats_cnae (cod_cnae_principal);
CREATE INDEX idx_mv_stats_cnae_total 
    ON mv_stats_cnae (total_estabelecimentos DESC);
```

### MV: Estatísticas por CNAE + Estado (Detalhada)

```sql
CREATE MATERIALIZED VIEW mv_stats_cnae_estado AS
SELECT 
    e.cod_cnae_principal,
    e.cod_estado_ibge,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL) AS com_email
FROM estabelecimento e
GROUP BY e.cod_cnae_principal, e.cod_estado_ibge;

CREATE UNIQUE INDEX idx_mv_stats_cnae_estado_pk 
    ON mv_stats_cnae_estado (cod_cnae_principal, cod_estado_ibge);
CREATE INDEX idx_mv_stats_cnae_estado_cnae 
    ON mv_stats_cnae_estado (cod_cnae_principal);
```

### MV: Empresas por Período de Abertura

```sql
CREATE MATERIALIZED VIEW mv_abertura_periodo AS
SELECT 
    DATE_TRUNC('month', e.data_inicio_atividade)::DATE AS mes_abertura,
    e.cod_estado_ibge,
    COUNT(*) AS total_aberturas,
    COUNT(DISTINCT e.cnpj_basico) AS empresas_unicas
FROM estabelecimento e
WHERE e.data_inicio_atividade IS NOT NULL
  AND e.data_inicio_atividade >= '2000-01-01'
GROUP BY DATE_TRUNC('month', e.data_inicio_atividade), e.cod_estado_ibge;

CREATE UNIQUE INDEX idx_mv_abertura_pk 
    ON mv_abertura_periodo (mes_abertura, cod_estado_ibge);
CREATE INDEX idx_mv_abertura_mes 
    ON mv_abertura_periodo (mes_abertura);
```

### MV: Top CNAEs por Cidade (Para autocomplete)

```sql
CREATE MATERIALIZED VIEW mv_top_cnaes_cidade AS
SELECT DISTINCT ON (cod_cidade_ibge, ranking)
    e.cod_cidade_ibge,
    e.cod_cnae_principal,
    c.nome_cnae,
    COUNT(*) AS total,
    ROW_NUMBER() OVER (PARTITION BY e.cod_cidade_ibge ORDER BY COUNT(*) DESC) AS ranking
FROM estabelecimento e
JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
WHERE e.cod_situacao_cadastral = '02'
GROUP BY e.cod_cidade_ibge, e.cod_cnae_principal, c.nome_cnae
HAVING COUNT(*) >= 10;

CREATE INDEX idx_mv_top_cnaes_cidade 
    ON mv_top_cnaes_cidade (cod_cidade_ibge, ranking);
```

## 6.2 Estratégia de Refresh

```sql
-- Criar função para refresh concorrente
CREATE OR REPLACE FUNCTION refresh_all_mvs()
RETURNS void AS $$
BEGIN
    -- MVs menores primeiro (mais rápido)
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_estado;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae;
    
    -- MVs maiores
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_municipio;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cnae_estado;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_abertura_periodo;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_top_cnaes_cidade;
END;
$$ LANGUAGE plpgsql;

-- Agendar via pg_cron (ou crontab externo)
-- Rodar quinzenalmente às 3h da manhã
SELECT cron.schedule('refresh_mvs', '0 3 1,15 * *', 'SELECT refresh_all_mvs()');
```

| MV | Periodicidade | Tempo Estimado |
|----|---------------|----------------|
| `mv_stats_estado` | Diário | ~2 min |
| `mv_stats_municipio` | Diário | ~5 min |
| `mv_stats_cnae` | Diário | ~3 min |
| `mv_stats_cnae_estado` | Semanal | ~10 min |
| `mv_abertura_periodo` | Semanal | ~8 min |
| `mv_top_cnaes_cidade` | Semanal | ~15 min |

## 6.3 Concorrência e janelas de atualização

- **Quinzenal obrigatório**: mesmo que haja refresh diário, manter rotina full nos dias 1 e 15 (03:00) para recalcular totais históricos, garantindo alinhamento aos requisitos do usuário.
- **REFRESH CONCURRENTLY** exige índice único em cada MV; todos os exemplos acima já incluem.
- **Ordem de execução**: rodar primeiro as views pequenas (`estado`, `cnae`) para liberar locks rapidamente, depois as maiores com hint `SET maintenance_work_mem TO '1GB'`.
- **Janela de manutenção**: estimar 30 minutos para o pacote completo; durante refresh concorrente, consultas continuam atendidas, mas operações pesadas devem ser evitadas.

---

# 7. Proposta Completa e Implementação

## 7.1 Resumo Executivo

### Principais Decisões

| Decisão | Escolha | Justificativa |
|---------|---------|---------------|
| **Tabelas UNLOGGED** | ✅ Sim (durante ETL) | 3-5x mais rápido na carga |
| **Índices** | 39 índices otimizados | Cobertura completa dos filtros |
| **CNPJ Completo** | Computado no Python durante carga | Busca exata O(1) sem concatenação |
| **Busca Textual** | GIN + pg_trgm | ILIKE performático (nome fantasia, razão social, nome do sócio) |
| **Datas** | BRIN + BTREE | Híbrido para diferentes queries |
| **MVs** | 6 views principais | Estatísticas pré-computadas |
| **Desnormalização** | Códigos IBGE + porte/natureza/flags + CNPJ completo | Evita JOINs sem inflar storage |

### Impacto Estimado

| Métrica | Antes (Estimado) | Depois (Esperado) |
|---------|------------------|-------------------|
| Tempo ETL completo | 8-12 horas | **2-3 horas** |
| Query filtro simples | 5-30 segundos | **< 500ms** |
| Query filtro composto | 30-120 segundos | **< 2 segundos** |
| Busca textual ILIKE | 60+ segundos | **< 3 segundos** |
| Estatísticas agregadas | 5-10 minutos | **< 100ms** (via MV) |
| Espaço total (dados+índices) | ~65 GB | ~93 GB |
| Busca exata por CNPJ completo | Concatenação em runtime | **< 1ms** (via índice único) |

## 7.2 Proposta Técnica Final

### Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ARQUITETURA PROPOSTA                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │   TABELAS       │    │    ÍNDICES      │    │      MVs        │         │
│  │   PRINCIPAIS    │    │   OTIMIZADOS    │    │  ESTATÍSTICAS   │         │
│  ├─────────────────┤    ├─────────────────┤    ├─────────────────┤         │
│  │ estabelecimento │───►│ 15 índices      │    │ mv_stats_mun    │         │
│  │ (58M rows)      │    │ BTREE/GIN/BRIN  │    │ mv_stats_estado │         │
│  │ UNLOGGED→LOGGED │    │                 │    │ mv_stats_cnae   │         │
│  ├─────────────────┤    ├─────────────────┤    │ mv_cnae_estado  │         │
│  │ empresa         │───►│ 5 índices       │    │ mv_abertura     │         │
│  │ (58M rows)      │    │                 │    │ mv_top_cnaes    │         │
│  ├─────────────────┤    ├─────────────────┤    └─────────────────┘         │
│  │ estab_cnae_sec  │───►│ 3 índices       │                                │
│  │ (180M rows)     │    │ covering        │    ┌─────────────────┐         │
│  ├─────────────────┤    ├─────────────────┤    │   AUXILIARES    │         │
│  │ socio           │───►│ 3 índices       │    ├─────────────────┤         │
│  │ (25M rows)      │    │                 │    │ ibge_regiao (5) │         │
│  ├─────────────────┤    └─────────────────┘    │ ibge_estado(27) │         │
│  │ simples         │                           │ ibge_cidade(5K) │         │
│  │ (40M rows)      │                           │ cnae (1.3K)     │         │
│  └─────────────────┘                           │ natureza (90)   │         │
│                                                │ pais (250)      │         │
│                                                │ motivo (80)     │         │
│                                                │ qualif. (70)    │         │
│                                                └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### ETL Pipeline

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         ETL PIPELINE OTIMIZADO                             │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  [1] DOWNLOAD (paralelo)                                                   │
│      └─► 37 arquivos ZIP (~5GB total)                                     │
│          └─► 10 workers simultâneos                                        │
│                                                                            │
│  [2] PREPARAÇÃO                                                            │
│      ├─► Criar DB com config otimizada                                    │
│      ├─► DROP tables existentes                                           │
│      ├─► CREATE UNLOGGED tables (sem PK/FK/índices)                       │
│      └─► Carregar tabelas IBGE (via HashMap O(1))                         │
│                                                                            │
│  [3] CARGA MASSIVA                                                         │
│      ├─► Processar ZIPs: auxiliares → empresas → estabelecimentos        │
│      ├─► COPY FROM STDIN (batch 500K linhas)                              │
│      ├─► 4 workers de inserção (I/O bound)                                │
│      ├─► Enriquecer com códigos IBGE durante leitura                      │
│      └─► Extrair CNAEs secundários para tabela separada                   │
│                                                                            │
│  [4] PÓS-PROCESSAMENTO                                                     │
│      ├─► Apply patches (dados inconsistentes RFB)                         │
│      ├─► DELETE duplicatas                                                │
│      ├─► ADD PRIMARY KEYs                                                 │
│      ├─► CREATE INDEXes (paralelo, maintenance_work_mem alto)             │
│      ├─► ADD FOREIGN KEYs                                                 │
│      ├─► VACUUM ANALYZE                                                   │
│      └─► (opcional) ALTER TABLE SET LOGGED                                │
│                                                                            │
│  [5] MATERIALIZAÇÃO                                                        │
│      └─► CREATE MATERIALIZED VIEWs + índices                              │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## 7.3 Checklist de Implementação

### Fase 1: Configuração de Ambiente
- [ ] Ajustar `postgresql.conf` para carga massiva
- [ ] Configurar `shared_buffers`, `work_mem`, `maintenance_work_mem`
- [ ] Desabilitar `autovacuum` temporariamente
- [ ] Configurar `wal_level = minimal` e `synchronous_commit = off`

### Fase 2: Modificação do Schema
- [ ] Atualizar `schema.py` com novos índices
- [ ] Adicionar coluna `cnpj_completo CHAR(14) NOT NULL` em `estabelecimento` e `estabelecimento_cnae_sec`
- [ ] Adicionar colunas desnormalizadas (`tem_contato`, `qtd_cnaes_secundarios`)
- [ ] Remover índices que serão recriados depois

### Fase 3: Otimização do ETL
- [ ] Implementar batch size de 500K (atualmente 250K)
- [ ] Usar TAB como delimitador no COPY (mais rápido que `;`)
- [ ] Implementar COPY com `FREEZE` para tabelas vazias
- [ ] Otimizar `IBGELookup` com `__slots__` para economia de memória
- [ ] Implementar função `compute_cnpj_completo()` em `db_transformers.py`
- [ ] Integrar computação de `cnpj_completo` no fluxo de transformação para `estabelecimento` e `estabelecimento_cnae_sec`

### Fase 4: Índices
- [ ] Criar script SQL com todos os 39 índices (incluindo índices de `cnpj_completo`)
- [ ] Usar `CREATE INDEX CONCURRENTLY` em produção
- [ ] Criar índices compostos conforme seção 5.2
- [ ] Criar índice único `idx_estab_cnpj_completo` em `estabelecimento`
- [ ] Criar índice `idx_cnae_sec_cnpj_completo` em `estabelecimento_cnae_sec`

### Fase 5: Materialized Views
- [ ] Criar as 6 MVs propostas
- [ ] Configurar refresh automático (pg_cron ou crontab)
- [ ] Criar índices nas MVs

### Fase 6: Validação
- [ ] Executar queries de teste em cada categoria de filtro
- [ ] Comparar EXPLAIN ANALYZE antes/depois
- [ ] Validar integridade referencial
- [ ] Documentar métricas de performance

## 7.4 Plano de Performance

### Testes Obrigatórios

```sql
-- TESTE 1: Filtro por Estado + CNAE + Situação Ativa
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT COUNT(*) 
FROM estabelecimento 
WHERE cod_estado_ibge = 35 
  AND cod_cnae_principal = '4711302'
  AND cod_situacao_cadastral = '02';
-- Esperado: Index Scan, < 50ms

-- TESTE 2: Busca textual em razão social
EXPLAIN (ANALYZE, BUFFERS)
SELECT cnpj_basico, razao_social 
FROM empresa 
WHERE razao_social ILIKE '%petrobras%'
LIMIT 100;
-- Esperado: Bitmap Index Scan (GIN), < 500ms

-- TESTE 2b: Busca textual em nome do sócio
EXPLAIN (ANALYZE, BUFFERS)
SELECT s.cnpj_basico, s.nome_socio, s.cnpj_cpf_socio, e.razao_social
FROM socio s
LEFT JOIN empresa e ON s.cnpj_basico = e.cnpj_basico
WHERE s.nome_socio ILIKE '%silva%'
LIMIT 100;
-- Esperado: Bitmap Index Scan (GIN), < 500ms

-- TESTE 3: JOIN estabelecimento + empresa com filtros
EXPLAIN (ANALYZE, BUFFERS)
SELECT e.*, emp.razao_social, emp.capital_social
FROM estabelecimento e
JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
WHERE e.cod_cidade_ibge = 3550308  -- São Paulo
  AND e.cod_cnae_principal = '6201501'  -- Desenvolvimento de software
  AND e.cod_situacao_cadastral = '02'
  AND emp.capital_social >= 100000;
-- Esperado: Nested Loop + Index Scan, < 1s

-- TESTE 4: Consulta com CNAE secundário
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT e.cnpj_basico, e.nome_fantasia
FROM estabelecimento e
WHERE EXISTS (
    SELECT 1 FROM estabelecimento_cnae_sec sec
    WHERE sec.cnpj_basico = e.cnpj_basico
      AND sec.cnpj_ordem = e.cnpj_ordem
      AND sec.cnpj_dv = e.cnpj_dv
      AND sec.cod_cnae = '4711302'
)
AND e.cod_estado_ibge = 35
LIMIT 1000;
-- Esperado: Semi Join + Index Scan, < 2s

-- TESTE 5: Estatísticas via MV
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM mv_stats_estado ORDER BY total_estabelecimentos DESC;
-- Esperado: Seq Scan na MV (pequena), < 10ms

-- TESTE 6: Range de datas
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*), DATE_TRUNC('month', data_inicio_atividade) as mes
FROM estabelecimento
WHERE data_inicio_atividade BETWEEN '2024-01-01' AND '2024-12-31'
  AND cod_estado_ibge = 35
GROUP BY DATE_TRUNC('month', data_inicio_atividade);
-- Esperado: BRIN scan + Index Scan, < 500ms

-- TESTE 7: Busca exata por CNPJ completo
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM estabelecimento 
WHERE cnpj_completo = '12345678000100';
-- Esperado: Index Scan (ou Index Scan usando Hash), < 1ms

-- TESTE 7b: JOIN usando CNPJ completo
EXPLAIN (ANALYZE, BUFFERS)
SELECT e.*, sec.cod_cnae
FROM estabelecimento e
JOIN estabelecimento_cnae_sec sec ON e.cnpj_completo = sec.cnpj_completo
WHERE e.cnpj_completo = '12345678000100';
-- Esperado: Nested Loop + Index Scan, < 5ms
```

### Cenários Críticos

| Cenário | Query | Tempo Máximo Aceitável |
|---------|-------|------------------------|
| Dashboard home | Total por estado | < 50ms (via MV) |
| Filtro simples | Estado + CNAE | < 200ms |
| Filtro completo | 5+ critérios | < 2s |
| Busca textual | ILIKE nome fantasia/razão social | < 1s |
| Busca textual | ILIKE nome do sócio | < 1s |
| Busca exata | CNPJ completo (lookup único) | < 1ms |
| Export (100K) | Filtro + ORDER | < 30s |
| Relatório CNAE | Agregação completa | < 5s (via MV) |

### Benchmarks Esperados

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          BENCHMARKS ESPERADOS                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ETL COMPLETO (primeira carga)                                             │
│  ├─ Download:           30-45 min (10 workers, ~100 Mbps)                  │
│  ├─ Carga tabelas aux:   2-5 min                                           │
│  ├─ Carga empresas:     15-20 min (58M rows)                               │
│  ├─ Carga estabelec.:   45-60 min (58M rows + enrich)                      │
│  ├─ Carga CNAEs sec.:   30-40 min (180M rows)                              │
│  ├─ Carga sócios:       10-15 min (25M rows)                               │
│  ├─ Patches/dedup:       5-10 min                                          │
│  ├─ Criar índices:      30-45 min (36 índices)                             │
│  ├─ Criar FKs:           5-10 min                                          │
│  ├─ VACUUM ANALYZE:     10-15 min                                          │
│  └─ Criar MVs:          15-20 min                                          │
│  ────────────────────────────────────────────────────────────────────────  │
│  TOTAL ESTIMADO:        2.5 - 4 horas                                      │
│                                                                             │
│  QUERIES (p95 latency)                                                      │
│  ├─ Filtro único:        < 100ms                                           │
│  ├─ Filtro composto:     < 500ms                                           │
│  ├─ Filtro + paginação:  < 300ms                                           │
│  ├─ Busca texto:         < 1s                                              │
│  ├─ Agregação simples:   < 2s                                              │
│  └─ MV lookup:           < 50ms                                            │
│                                                                             │
│  STORAGE                                                                    │
│  ├─ Dados:              ~65 GB                                             │
│  ├─ Índices:            ~25 GB                                             │
│  ├─ MVs:                ~2 GB                                              │
│  └─ TOTAL:              ~92 GB                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 8. Operação Híbrida Mac/Ubuntu (NVMe + Postgres em container)

## 8.1 Perfis de hardware e implicações

| Ambiente | CPU | RAM disponível | Armazenamento | Observações |
|----------|-----|----------------|---------------|-------------|
| **Local (MacBook Pro M3 Pro)** | 11‑12 núcleos de performance + eficiência | 18 GB | NVMe interno | Alta largura de banda single-thread, thermal throttling moderado, permite buffers maiores. |
| **Produção (Ubuntu + Redsnet)** | 8vCPU (x86) | 8 GB | NVMe dedicado | Mais estável sob carga contínua, porém memória é o gargalo; o container do Postgres compartilha a mesma RAM. |

**Premissas-chave**
- Ambos os ambientes usam **o mesmo stack** (Python + Postgres em container), logo o código precisa apenas **expor ajustes parametrizáveis**, e não ifs específicos de SO.
- O gargalo muda conforme o ambiente: no Mac o custo é CPU/GIL e serialização para o `COPY`; no Ubuntu o custo é RAM e concorrência com o Postgres.
- Os dois utilizam NVMe, então otimizações de disco focam em **acessar menos dados simultâneos** ao invés de priorizar caching.

## 8.2 Ajustes recomendados no código Python

### 1. Transformação fora da etapa de leitura
`utils/db_batch_producer.py` chama `transform_batch` ainda dentro do produtor antes de enfileirar (`transform_batch(item, sanitizer_func)` nas linhas 88 e 106). Ao mover essa transformação para `postgres_loader.consume_batches` (antes de `convert_rows_to_csv_buffer`) é possível:
- usar o poder de CPU do Mac (mais threads consumidores concorrentes);
- aliviar o servidor Ubuntu (produtor mantém batches menores e empurra o trabalho pesado para no máximo `WORKER_THREADS` consumidores);
- evitar múltiplas cópias da mesma lista na RAM.

### 2. Lotes elásticos
`config.BATCH_SIZE` é um valor global de 250 mil linhas, e `QUEUE_SIZE` depende apenas de `cpu_count()`. Introduza um _helper_ que calcule ambos em runtime:

```
target_mem_gb = psutil.virtual_memory().total / 1e9
if target_mem_gb <= 8:
    batch_size = 120_000
    queue_size = 2 * workers
else:
    batch_size = 400_000
    queue_size = 4 * workers
```

Esse ajuste mantém o Mac sempre alimentado (menos commits/fsync) e evita estourar a RAM do servidor. O valor atual pode continuar como padrão para ambientes desconhecidos, mas permitir override via `ETL_BATCH_SIZE` / `ETL_QUEUE_SIZE` (variáveis de ambiente simples de ler em `config.py`).

### 3. Concorrência controlada
`WORKER_THREADS = cpu_count() - 1` pode gerar até 11 conexões no Mac (cada uma com `maintenance_work_mem` + buffers). Define um limite superior de 6 consumidores e inferior de 2. O servidor Ubuntu deve rodar com 3 threads (garante espaço para o próprio Postgres e para o processo Docker). Exponha esse limite via `ETL_MAX_WORKERS` para alinhar ambientes sem alteração de código.

### 4. Reuso de buffers de COPY
`convert_rows_to_csv_buffer` cria `StringIO` + `BytesIO` a cada batch. Reaproveitar o `StringIO` (limpando com `seek(0); truncate(0)`) reduz o overhead de GC, especialmente no Mac que consegue rodar batches maiores por vez. Combine com a flag `low_memory` para decidir se os buffers serão reaproveitados ou recriados.

### 5. Sanitização incremental
`sanitize_for_postgres` percorre todas as colunas antes de qualquer filtro. Converter isso para um gerador (yield por linha) reduz picos de memória e deixa o servidor Ubuntu menos pressionado. O Mac continua se beneficiando por conseguir sanitizar mais linhas/segundo.

### 6. Controle fino do pipeline
- `produce_batches(... parallel=True)` cria até 4 _threads_ produtoras; permita regular esse valor (`ETL_MAX_PRODUCERS`). No Mac use 4 para aproveitar a largura de banda do NVMe; no Ubuntu limite a 2 para reduzir contexto.
- Continue usando `--low-memory` quando rodar no Ubuntu e mantenha `parallel=True` no Mac. As duas flags já existem em `orchestrator.run_orchestrator`, basta documentar o perfil recomendado.

### 7. Criação de índices paralela e consciente
`PostgresBuilder.create_indexes()` executa sequencialmente. Encapsular a criação em uma `ThreadPoolExecutor` (máx. 4 no Mac, 2 no Ubuntu) com `CREATE INDEX CONCURRENTLY` permite aproveitar ambos ambientes sem alterar Docker. A função pode receber `max_workers` via env `ETL_INDEX_WORKERS`.

## 8.3 Parâmetros sugeridos por ambiente

| Parâmetro | Mac (18 GB) | Ubuntu (8 GB) | Implementação sugerida |
|-----------|-------------|---------------|------------------------|
| `BATCH_SIZE` | 400 000 linhas (estabelecimento com `ratio=0.4` = 160 k) | 120 000 (estab ≈ 48 k) | Calcular dinamicamente lendo `ETL_BATCH_SIZE` ou usando heurística por RAM. |
| `WORKER_THREADS` | 5-6 | 3 | Limitar via `min(max(cpu_count()-1, 2), ETL_MAX_WORKERS)`. |
| `QUEUE_SIZE` | `workers * 4` | `workers * 2` | Ajustar após computar `workers`. |
| Produtores (`produce_batches`) | 4 threads | 2 threads | Expor `ETL_MAX_PRODUCERS`. |
| Flag `low_memory` | `False` | `True` | Usar argumento CLI `--low-memory` no Ubuntu. |
| Criação de índices | Paralelo (`max_workers=4`) | Paralelo (`max_workers=2`) | Aplicar no `PostgresBuilder`. |
| `maintenance_work_mem` (SQL) | 3 GB temporário | 1 GB temporário | Já documentado; alinhar com recursos disponíveis (Postgres em container). |

## 8.4 Passos práticos de implementação
1. **Parâmetros dinâmicos**: ler variáveis de ambiente (`ETL_BATCH_SIZE`, `ETL_MAX_WORKERS`, `ETL_MAX_PRODUCERS`, `ETL_QUEUE_SIZE`) em `config.py` com _fallback_ para a heurística por RAM (via `psutil`).
2. **Split transformação/inserção**: mover `transform_batch` para os consumidores e enfileirar apenas as linhas brutas, reduzindo cópias e liberando CPU no Mac.
3. **Buffer pooling**: introduzir um pequeno _pool_ de `BytesIO`/`StringIO` (lista simples) quando `low_memory=False`; no Ubuntu, manter o comportamento atual para evitar retenção.
4. **Monitoramento**: registrar métricas simples (tempo por batch, tempo de COPY, média de memória) com `utils.logger.print_log`. Isso facilita comparar execuções entre ambientes e ajustar os env vars conforme necessário.
5. **Documentar perfis**: adicionar ao README/CLI a tabela acima explicando como exportar os env vars antes de executar `python etl.py complete ...`.

Com essas alterações o mesmo código Python se adapta automaticamente aos dois cenários, explorando o poder de CPU/RAM do Mac para acelerar desenvolvimentos e garantindo que a execução no servidor (mais limitado em memória) mantenha estabilidade e previsibilidade.

---

# Anexo A: Scripts SQL Completos

## A.1 Script de Criação de Índices

```sql
-- indices_otimizados.sql
-- Executar após carga completa dos dados

\echo 'Iniciando criação de índices otimizados...'
\timing on

-- Habilitar extensão para busca textual
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Configurar paralelismo
SET max_parallel_maintenance_workers = 4;
SET maintenance_work_mem = '2GB';

-- ============================================
-- ÍNDICES: ESTABELECIMENTO
-- ============================================

\echo 'Criando índices de localização...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cidade_ibge 
    ON estabelecimento (cod_cidade_ibge);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_estado_ibge 
    ON estabelecimento (cod_estado_ibge);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_regiao_ibge 
    ON estabelecimento (cod_regiao_ibge);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cep 
    ON estabelecimento (cep);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_ddd 
    ON estabelecimento (ddd_telefone_1);

\echo 'Criando índices de CNAE...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_principal 
    ON estabelecimento (cod_cnae_principal);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_estado 
    ON estabelecimento (cod_cnae_principal, cod_estado_ibge);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnae_cidade 
    ON estabelecimento (cod_cnae_principal, cod_cidade_ibge);

\echo 'Criando índices de data (BRIN + BTREE)...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_inicio_brin 
    ON estabelecimento USING BRIN (data_inicio_atividade) WITH (pages_per_range = 32);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_data_inicio 
    ON estabelecimento (data_inicio_atividade);

\echo 'Criando índices de situação...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_situacao 
    ON estabelecimento (cod_situacao_cadastral);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_ativas 
    ON estabelecimento (cod_situacao_cadastral) WHERE cod_situacao_cadastral = '02';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_matriz_filial 
    ON estabelecimento (matriz_filial);

\echo 'Criando índices de busca textual (GIN)...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_nome_fantasia_trgm 
    ON estabelecimento USING GIN (nome_fantasia gin_trgm_ops);

\echo 'Criando índices de contato...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_email 
    ON estabelecimento (email) WHERE email IS NOT NULL AND email != '';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_telefone 
    ON estabelecimento (telefone_1) WHERE telefone_1 IS NOT NULL;

\echo 'Criando índices de CNPJ completo...'
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnpj_completo 
    ON estabelecimento (cnpj_completo);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_cnpj_completo_hash 
    ON estabelecimento USING HASH (cnpj_completo);

\echo 'Criando índices compostos...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_prospeccao 
    ON estabelecimento (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_estab_leads_email 
    ON estabelecimento (cod_cnae_principal, cod_situacao_cadastral)
    WHERE email IS NOT NULL AND email != '';

-- ============================================
-- ÍNDICES: EMPRESA
-- ============================================

\echo 'Criando índices da tabela empresa...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_porte 
    ON empresa (cod_porte);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_natureza 
    ON empresa (cod_natureza_juridica);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_capital 
    ON empresa (capital_social);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_empresa_razao_social_trgm 
    ON empresa USING GIN (razao_social gin_trgm_ops);

-- ============================================
-- ÍNDICES: CNAE SECUNDÁRIO
-- ============================================

\echo 'Criando índices da tabela cnae_secundario...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnae 
    ON estabelecimento_cnae_sec (cod_cnae);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_estab 
    ON estabelecimento_cnae_sec (cnpj_basico, cnpj_ordem, cnpj_dv);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_cnpj_completo 
    ON estabelecimento_cnae_sec (cnpj_completo);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cnae_sec_covering 
    ON estabelecimento_cnae_sec (cod_cnae) INCLUDE (cnpj_basico, cnpj_ordem, cnpj_dv);

-- ============================================
-- ÍNDICES: SÓCIO
-- ============================================

\echo 'Criando índices da tabela socio...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_cpf_cnpj 
    ON socio (cnpj_cpf_socio);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_trgm 
    ON socio USING GIN (nome_socio gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_nome_prefix 
    ON socio (nome_socio varchar_pattern_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_socio_empresa 
    ON socio (cnpj_basico);

\echo 'Índices criados com sucesso!'
\timing off
```

## A.2 Script de Criação de MVs

```sql
-- materialized_views.sql

\echo 'Criando Materialized Views...'
\timing on

-- MV: Estatísticas por Estado
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_estado AS
SELECT 
    e.cod_estado_ibge,
    est.sigla_uf,
    est.nome_estado,
    e.cod_regiao_ibge,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email,
    COUNT(*) FILTER (WHERE e.telefone_1 IS NOT NULL) AS com_telefone
FROM estabelecimento e
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY e.cod_estado_ibge, est.sigla_uf, est.nome_estado, e.cod_regiao_ibge;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_stats_estado_pk 
    ON mv_stats_estado (cod_estado_ibge);

-- MV: Estatísticas por Município
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_municipio AS
SELECT 
    e.cod_cidade_ibge,
    c.nome_cidade,
    e.cod_estado_ibge,
    est.sigla_uf,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(*) FILTER (WHERE e.matriz_filial = '1') AS matrizes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email
FROM estabelecimento e
LEFT JOIN ibge_cidade c ON e.cod_cidade_ibge = c.cod_cidade_ibge
LEFT JOIN ibge_estado est ON e.cod_estado_ibge = est.cod_estado_ibge
GROUP BY e.cod_cidade_ibge, c.nome_cidade, e.cod_estado_ibge, est.sigla_uf;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_stats_municipio_pk 
    ON mv_stats_municipio (cod_cidade_ibge);
CREATE INDEX IF NOT EXISTS idx_mv_stats_municipio_estado 
    ON mv_stats_municipio (cod_estado_ibge);

-- MV: Estatísticas por CNAE
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_cnae AS
SELECT 
    e.cod_cnae_principal,
    c.nome_cnae,
    COUNT(*) AS total_estabelecimentos,
    COUNT(*) FILTER (WHERE e.cod_situacao_cadastral = '02') AS ativos,
    COUNT(DISTINCT e.cod_estado_ibge) AS estados_presentes,
    COUNT(DISTINCT e.cod_cidade_ibge) AS cidades_presentes,
    COUNT(*) FILTER (WHERE e.email IS NOT NULL AND e.email != '') AS com_email
FROM estabelecimento e
LEFT JOIN cnae c ON e.cod_cnae_principal = c.cod_cnae
GROUP BY e.cod_cnae_principal, c.nome_cnae;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_stats_cnae_pk 
    ON mv_stats_cnae (cod_cnae_principal);
CREATE INDEX IF NOT EXISTS idx_mv_stats_cnae_total 
    ON mv_stats_cnae (total_estabelecimentos DESC);

\echo 'Materialized Views criadas com sucesso!'
\timing off
```

---

# Anexo B: Código Python Otimizado

## B.1 Função para Computar CNPJ Completo

```python
# utils/db_transformers.py

def compute_cnpj_completo(rows: List[List], columns: List[str]) -> List[List]:
    """
    Computa cnpj_completo concatenando cnpj_basico + cnpj_ordem + cnpj_dv.
    
    Esta função deve ser chamada durante o processo de transformação,
    ANTES do COPY para o banco de dados.
    
    Args:
        rows: Lista de linhas (cada linha é uma lista de valores)
        columns: Lista com os nomes das colunas na ordem correspondente
    
    Returns:
        Lista de linhas com cnpj_completo preenchido
    
    Exemplo:
        Input:  cnpj_basico='12345678', cnpj_ordem='0001', cnpj_dv='00'
        Output: cnpj_completo='12345678000100'
    """
    # Encontrar índices das colunas CNPJ
    try:
        idx_basico = columns.index('cnpj_basico')
        idx_ordem = columns.index('cnpj_ordem')
        idx_dv = columns.index('cnpj_dv')
        idx_completo = columns.index('cnpj_completo')
    except ValueError as e:
        raise ValueError(f"Coluna CNPJ não encontrada no schema: {e}")
    
    new_rows = []
    for row in rows:
        row = list(row)
        
        # Garantir que os valores são strings e preencher com zeros à esquerda se necessário
        basico = str(row[idx_basico] or '').strip().zfill(8)[:8]
        ordem = str(row[idx_ordem] or '').strip().zfill(4)[:4]
        dv = str(row[idx_dv] or '').strip().zfill(2)[:2]
        
        # Concatenar e garantir exatamente 14 caracteres
        cnpj_completo = (basico + ordem + dv).ljust(14, '0')[:14]
        row[idx_completo] = cnpj_completo
        new_rows.append(row)
    
    return new_rows


# Integração no transform_batch
def transform_batch(item: dict, sanitizer_func: Callable) -> List:
    """
    Aplica todas as transformações necessárias a um lote de dados.
    """
    table = item["table"]
    columns = item["columns"]
    rows = item["rows"]

    rows = sanitizer_func(rows)

    if table == "empresa":
        rows = normalize_numeric_br(rows, columns, ["capital_social"])

    elif table == "estabelecimento":
        rows = normalize_dates(rows, columns, [
            "data_situacao_cadastral", "data_inicio_atividade", "data_situacao_especial"
        ])
        rows = IBGE_LOOKUP.append_ibge_to_estabelecimentos(rows, columns)
        # Computar CNPJ completo ANTES do COPY
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "estabelecimento_cnae_sec":
        # Computar CNPJ completo para CNAEs secundários também
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "simples":
        rows = normalize_dates(
            rows, columns,
            ["data_opcao_simples", "data_exclusao_simples", "data_opcao_mei", "data_exclusao_mei"]
        )

    elif table == "socio":
        rows = normalize_dates(rows, columns, ["data_entrada_sociedade"])

    return rows
```

## B.2 IBGELookup com __slots__

```python
# utils/ibge_lookup_optimized.py

class IBGERecord:
    """Record otimizado com __slots__ para economia de memória."""
    __slots__ = ('cod', 'nome', 'extra')
    
    def __init__(self, cod, nome, extra=None):
        self.cod = cod
        self.nome = nome
        self.extra = extra


class OptimizedIBGELookup:
    """
    Lookup IBGE otimizado para 200M+ operações.
    Usa arrays densos ao invés de dicts quando possível.
    """
    
    def __init__(self):
        # Estados: array indexado por código (11-53)
        self._estados = [None] * 54  # índice direto
        # Cidades: dict com SIAFI como chave (mais esparso)
        self._cidades = {}
        # Cache de UF -> código estado
        self._uf_to_cod = {}
        
    def get_estado(self, cod_estado: int):
        """O(1) lookup direto no array."""
        if 11 <= cod_estado <= 53:
            return self._estados[cod_estado]
        return None
    
    def get_cidade_by_siafi(self, siafi: str):
        """O(1) lookup no dict."""
        return self._cidades.get(siafi) or self._cidades.get(siafi.lstrip('0'))
```
