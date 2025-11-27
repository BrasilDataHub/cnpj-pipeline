# Relatório de Otimização do Banco de Dados

## Base Empresarial - Análise de Performance e Recomendações

**Data:** Novembro 2024  
**Versão:** 1.0

---

## 📊 Resumo Executivo

Este documento apresenta uma análise completa da estrutura do banco de dados `companies_pgsql` com foco na otimização de consultas para os filtros básicos e avançados da aplicação. O objetivo é garantir que todas as buscas possam ser realizadas de forma satisfatória, especialmente em municípios com grande volume de empresas como São Paulo e Brasília.

### Principais Descobertas

1. **Índices parciais limitados**: Os índices existentes para empresas ativas (`cod_situacao_cadastral = '02'`) funcionam bem, mas consultas para empresas inativas sofrem com scans de tabela completa.

2. **Filtros combinados sem suporte de índice**: Combinações frequentes de filtros (cidade + situação + porte, cidade + regime tributário) não possuem índices compostos otimizados.

3. **Tabela `simples` sem índices adequados**: Filtros por regime tributário (Simples Nacional/MEI) dependem de scans completos.

4. **Índices não utilizados**: Vários índices criados não estão sendo utilizados pelo query planner, representando overhead de armazenamento.

---

## 📈 Estatísticas do Banco

### Volume de Dados

| Tabela | Registros | Tamanho Tabela | Tamanho Índices | Total |
|--------|-----------|----------------|-----------------|-------|
| `estabelecimento` | ~68,5M | 14 GB | 26 GB | 41 GB |
| `empresa` | ~65,3M | 5,7 GB | 16 GB | 22 GB |
| `estabelecimento_cnae_sec` | ~113,5M | 7,4 GB | 9,2 GB | 16 GB |
| `simples` | ~45,8M | 2,4 GB | 1,4 GB | 3,8 GB |
| `socio` | ~26,4M | 3,0 GB | 3,5 GB | 6,5 GB |

### Distribuição de Situações Cadastrais (Exemplo: Brasília)

| Situação | Código | Quantidade | % |
|----------|--------|------------|---|
| Baixada | 08 | 532.994 | 45,1% |
| Ativa | 02 | 468.085 | 39,6% |
| Inapta | 04 | 172.765 | 14,6% |
| Suspensa | 03 | 4.983 | 0,4% |
| Nula | 01 | 2.240 | 0,2% |

---

## 🔍 Análise dos Filtros

### Filtros Básicos (Painel de Listagem)

| Filtro | Campos Usados | Status Atual |
|--------|---------------|--------------|
| Território (Região/Estado/Cidade) | `cod_regiao_ibge`, `cod_estado_ibge`, `cod_cidade_ibge` | ✅ OK |
| Situação Cadastral (Ativas) | `cod_situacao_cadastral = '02'` | ✅ OK |
| Situação Cadastral (Inativas) | `cod_situacao_cadastral != '02'` | ⚠️ Lento |
| Período de Abertura | `data_inicio_atividade` | ✅ OK |
| Busca por Nome | `razao_social`, `nome_fantasia` | ⚠️ Moderado |
| CNAE Principal | `cod_cnae_principal` | ✅ OK |
| Porte da Empresa | `empresa.cod_porte` | ⚠️ Lento |

### Filtros Avançados (Busca Avançada)

| Filtro | Campos Usados | Status Atual |
|--------|---------------|--------------|
| Múltiplas Localidades | `cod_regiao_ibge`, `cod_estado_ibge`, `cod_cidade_ibge` | ⚠️ Moderado |
| Atividades Econômicas (Principal + Secundária) | `cod_cnae_principal`, `estabelecimento_cnae_sec` | ⚠️ Lento |
| Tipo de Estabelecimento | `matriz_filial` | ✅ OK |
| Porte da Empresa | `empresa.cod_porte` | ⚠️ Lento |
| Natureza Jurídica | `empresa.cod_natureza_juridica` | ⚠️ Moderado |
| Regime Tributário | `simples.opcao_simples`, `simples.opcao_mei` | ❌ Muito Lento |
| Capital Social | `empresa.capital_social` | ⚠️ Moderado |
| Bairro | `bairro` (ILIKE) | ⚠️ Moderado |
| CEP | `cep` | ✅ OK |
| DDD | `ddd_telefone_1`, `ddd_telefone_2` | ⚠️ Moderado |
| Com Telefone | `telefone_1 IS NOT NULL` | ⚠️ Moderado |
| Com E-mail | `email IS NOT NULL` | ⚠️ Moderado |
| Sem "contab" no e-mail | `email NOT ILIKE '%contab%'` | ❌ Muito Lento |

---

## 🚨 Problemas Identificados

### 1. Consultas de Empresas Inativas Muito Lentas

**Problema**: O índice parcial `idx_estab_cidade_ativas_cnpj` só funciona quando `cod_situacao_cadastral = '02'`. Para outros status, o PostgreSQL faz scan completo pela primary key.

**Query Problemática**:
```sql
SELECT e.cnpj_completo
FROM estabelecimento e
WHERE e.cod_cidade_ibge = 5300108
AND e.cod_situacao_cadastral = '04'  -- Inapta
ORDER BY e.cnpj_completo
LIMIT 20;
```

**Tempo**: 334ms (deveria ser < 10ms)

**Plano de Execução**:
```
Index Scan using estabelecimento_pkey on estabelecimento e
  Filter: ((cod_cidade_ibge = 5300108) AND ((cod_situacao_cadastral)::text = '04'::text))
  Rows Removed by Filter: 10338
```

### 2. Filtro por Regime Tributário sem Índice

**Problema**: A tabela `simples` não tem índices para os campos `opcao_simples` e `opcao_mei`, causando scans completos em ~46 milhões de registros.

**Query Problemática**:
```sql
SELECT e.cnpj_completo
FROM estabelecimento e
JOIN simples s ON e.cnpj_basico = s.cnpj_basico
WHERE e.cod_cidade_ibge = 5300108
AND (s.opcao_simples = 'S' OR s.opcao_mei = 'S');
```

### 3. Filtro por Porte/Natureza Jurídica Requer JOIN

**Problema**: Filtros por `cod_porte` e `cod_natureza_juridica` exigem JOIN com a tabela `empresa`, sem índices compostos que otimizem a busca.

### 4. Índices Não Utilizados

Os seguintes índices têm **0 scans** e representam overhead:

- `idx_estab_telefone`
- `idx_estab_email_hash`
- `idx_estab_prospeccao`
- `idx_estab_temporal`
- `idx_estab_estado_ativas_cnpj`
- `idx_estab_regiao_ativas_cnpj`
- `idx_estab_leads_email`
- `idx_empresa_razao_social`
- `idx_empresa_porte`
- `idx_empresa_razao_social_trgm`
- `idx_empresa_capital`

---

## ✅ Recomendações de Otimização

### Nível 1: Alta Prioridade (Implementar Imediatamente)

#### 1.1 Índice Composto para Cidade + Situação Cadastral

```sql
-- Índice que suporta todas as situações cadastrais por cidade
CREATE INDEX CONCURRENTLY idx_estab_cidade_situacao_cnpj
ON estabelecimento (cod_cidade_ibge, cod_situacao_cadastral, cnpj_completo);

-- Estimativa de tamanho: ~2.5 GB
-- Impacto: Consultas de inativas de 334ms → <5ms
```

#### 1.2 Índice para Estado + Situação Cadastral

```sql
-- Para filtros de estado com diferentes situações
CREATE INDEX CONCURRENTLY idx_estab_estado_situacao_cnpj
ON estabelecimento (cod_estado_ibge, cod_situacao_cadastral, cnpj_completo);

-- Estimativa de tamanho: ~2.5 GB
```

#### 1.3 Índices na Tabela Simples para Regime Tributário

```sql
-- Índice para empresas do Simples Nacional
CREATE INDEX CONCURRENTLY idx_simples_opcao_simples
ON simples (cnpj_basico)
WHERE opcao_simples = 'S';

-- Índice para MEI
CREATE INDEX CONCURRENTLY idx_simples_opcao_mei
ON simples (cnpj_basico)
WHERE opcao_mei = 'S';

-- Estimativa de tamanho: ~800 MB cada
```

### Nível 2: Média Prioridade

#### 2.1 Índices para Filtros Combinados Frequentes

```sql
-- Cidade + CNAE + Situação (para filtros combinados)
CREATE INDEX CONCURRENTLY idx_estab_cidade_cnae_situacao
ON estabelecimento (cod_cidade_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- Estado + CNAE + Situação
CREATE INDEX CONCURRENTLY idx_estab_estado_cnae_situacao
ON estabelecimento (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);
```

#### 2.2 Índice Composto na Tabela Empresa para JOINs

```sql
-- Para otimizar filtros por porte quando combinado com estabelecimento
CREATE INDEX CONCURRENTLY idx_empresa_porte_cnpj
ON empresa (cod_porte, cnpj_basico);

-- Para filtros por natureza jurídica
CREATE INDEX CONCURRENTLY idx_empresa_natureza_cnpj
ON empresa (cod_natureza_juridica, cnpj_basico);
```

#### 2.3 Índice para DDD com Cobertura

```sql
-- Índice covering para filtros por DDD
CREATE INDEX CONCURRENTLY idx_estab_ddd1_covering
ON estabelecimento (ddd_telefone_1) 
INCLUDE (cnpj_completo, cod_cidade_ibge)
WHERE ddd_telefone_1 IS NOT NULL AND ddd_telefone_1 != '';

CREATE INDEX CONCURRENTLY idx_estab_ddd2_covering
ON estabelecimento (ddd_telefone_2)
INCLUDE (cnpj_completo, cod_cidade_ibge)
WHERE ddd_telefone_2 IS NOT NULL AND ddd_telefone_2 != '';
```

### Nível 3: Baixa Prioridade (Otimizações Adicionais)

#### 3.1 Índice GIN para Bairro (Busca por Texto)

```sql
-- Para buscas ILIKE em bairro
CREATE INDEX CONCURRENTLY idx_estab_bairro_trgm
ON estabelecimento USING gin (bairro gin_trgm_ops);
```

#### 3.2 Índice para Filtro de E-mail sem "contab"

```sql
-- Índice parcial para e-mails que NÃO contêm "contab"
CREATE INDEX CONCURRENTLY idx_estab_email_sem_contab
ON estabelecimento (cod_cidade_ibge, cnpj_completo)
WHERE email IS NOT NULL 
AND email != '' 
AND email NOT ILIKE '%contab%';
```

---

## 📊 Views Materializadas Sugeridas

### MV1: Estatísticas por Cidade e Situação

```sql
CREATE MATERIALIZED VIEW mv_stats_cidade_situacao AS
SELECT 
    cod_cidade_ibge,
    cod_situacao_cadastral,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE matriz_filial = '1') as matrizes,
    COUNT(*) FILTER (WHERE email IS NOT NULL AND email != '') as com_email,
    COUNT(*) FILTER (WHERE telefone_1 IS NOT NULL) as com_telefone
FROM estabelecimento
GROUP BY cod_cidade_ibge, cod_situacao_cadastral;

CREATE UNIQUE INDEX ON mv_stats_cidade_situacao (cod_cidade_ibge, cod_situacao_cadastral);

-- Refresh diário ou semanal
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cidade_situacao;
```

### MV2: Empresas com Regime Tributário por Cidade

```sql
CREATE MATERIALIZED VIEW mv_regime_tributario_cidade AS
SELECT 
    e.cod_cidade_ibge,
    e.cod_estado_ibge,
    e.cod_situacao_cadastral,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE s.opcao_simples = 'S') as simples_nacional,
    COUNT(*) FILTER (WHERE s.opcao_mei = 'S') as mei
FROM estabelecimento e
LEFT JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
LEFT JOIN simples s ON emp.cnpj_basico = s.cnpj_basico
GROUP BY e.cod_cidade_ibge, e.cod_estado_ibge, e.cod_situacao_cadastral;

CREATE UNIQUE INDEX ON mv_regime_tributario_cidade (cod_cidade_ibge, cod_situacao_cadastral);
```

### MV3: Empresas por Porte e Cidade

```sql
CREATE MATERIALIZED VIEW mv_porte_cidade AS
SELECT 
    e.cod_cidade_ibge,
    emp.cod_porte,
    e.cod_situacao_cadastral,
    COUNT(*) as total
FROM estabelecimento e
JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
GROUP BY e.cod_cidade_ibge, emp.cod_porte, e.cod_situacao_cadastral;

CREATE UNIQUE INDEX ON mv_porte_cidade (cod_cidade_ibge, cod_porte, cod_situacao_cadastral);
```

---

## 🗑️ Índices para Remoção

Os seguintes índices têm 0 utilizações e podem ser removidos para economizar espaço:

```sql
-- ATENÇÃO: Verificar se não são usados em ambientes de produção antes de remover

-- Candidatos para remoção (analisar individualmente):
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_telefone;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_email_hash;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_prospeccao;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_temporal;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_estado_ativas_cnpj;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_regiao_ativas_cnpj;
DROP INDEX CONCURRENTLY IF EXISTS idx_estab_leads_email;
DROP INDEX CONCURRENTLY IF EXISTS idx_empresa_razao_social;  -- Manter idx_empresa_razao_social_trgm
DROP INDEX CONCURRENTLY IF EXISTS idx_empresa_porte;  -- Será substituído por idx_empresa_porte_cnpj
DROP INDEX CONCURRENTLY IF EXISTS idx_empresa_capital;
```

**Economia Estimada**: ~3-5 GB

---

## 🔧 Script de Implementação Completo

```sql
-- ============================================
-- SCRIPT DE OTIMIZAÇÃO DO BANCO DE DADOS
-- Base Empresarial - companies_pgsql
-- ============================================

-- FASE 1: Índices de Alta Prioridade
-- Executar em horário de baixa utilização

-- 1.1 Cidade + Situação + CNPJ
CREATE INDEX CONCURRENTLY idx_estab_cidade_situacao_cnpj
ON estabelecimento (cod_cidade_ibge, cod_situacao_cadastral, cnpj_completo);

-- 1.2 Estado + Situação + CNPJ
CREATE INDEX CONCURRENTLY idx_estab_estado_situacao_cnpj
ON estabelecimento (cod_estado_ibge, cod_situacao_cadastral, cnpj_completo);

-- 1.3 Simples Nacional
CREATE INDEX CONCURRENTLY idx_simples_opcao_simples
ON simples (cnpj_basico)
WHERE opcao_simples = 'S';

-- 1.4 MEI
CREATE INDEX CONCURRENTLY idx_simples_opcao_mei
ON simples (cnpj_basico)
WHERE opcao_mei = 'S';

-- FASE 2: Índices de Média Prioridade

-- 2.1 Cidade + CNAE + Situação
CREATE INDEX CONCURRENTLY idx_estab_cidade_cnae_situacao
ON estabelecimento (cod_cidade_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- 2.2 Estado + CNAE + Situação
CREATE INDEX CONCURRENTLY idx_estab_estado_cnae_situacao
ON estabelecimento (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral);

-- 2.3 Empresa - Porte + CNPJ
CREATE INDEX CONCURRENTLY idx_empresa_porte_cnpj
ON empresa (cod_porte, cnpj_basico);

-- 2.4 Empresa - Natureza + CNPJ
CREATE INDEX CONCURRENTLY idx_empresa_natureza_cnpj
ON empresa (cod_natureza_juridica, cnpj_basico);

-- FASE 3: Views Materializadas

-- 3.1 Stats por cidade e situação
CREATE MATERIALIZED VIEW mv_stats_cidade_situacao AS
SELECT 
    cod_cidade_ibge,
    cod_situacao_cadastral,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE matriz_filial = '1') as matrizes,
    COUNT(*) FILTER (WHERE email IS NOT NULL AND email != '') as com_email,
    COUNT(*) FILTER (WHERE telefone_1 IS NOT NULL) as com_telefone
FROM estabelecimento
GROUP BY cod_cidade_ibge, cod_situacao_cadastral;

CREATE UNIQUE INDEX ON mv_stats_cidade_situacao (cod_cidade_ibge, cod_situacao_cadastral);

-- FASE 4: Remoção de índices não utilizados (OPCIONAL - verificar antes)
-- DROP INDEX CONCURRENTLY IF EXISTS idx_estab_telefone;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_estab_email_hash;
-- etc...

-- FASE 5: Atualizar estatísticas
ANALYZE estabelecimento;
ANALYZE empresa;
ANALYZE simples;
ANALYZE estabelecimento_cnae_sec;
```

---

## 📋 Plano de Execução

### Semana 1: Índices de Alta Prioridade
1. Criar `idx_estab_cidade_situacao_cnpj` (maior impacto)
2. Criar `idx_estab_estado_situacao_cnpj`
3. Criar `idx_simples_opcao_simples`
4. Criar `idx_simples_opcao_mei`
5. Testar filtros de empresas inativas
6. Testar filtros de regime tributário

### Semana 2: Índices de Média Prioridade
1. Criar índices compostos para CNAE + localização
2. Criar índices na tabela empresa
3. Testar filtros combinados

### Semana 3: Views Materializadas
1. Criar view `mv_stats_cidade_situacao`
2. Configurar job de refresh
3. Ajustar código para usar MVs quando apropriado

### Semana 4: Limpeza e Otimização
1. Analisar índices para remoção
2. Remover índices não utilizados
3. Executar VACUUM ANALYZE
4. Monitorar performance

---

## 📈 Estimativas de Melhoria

| Operação | Tempo Atual | Tempo Esperado | Melhoria |
|----------|-------------|----------------|----------|
| Listar inativas por cidade | 300-500ms | <10ms | 98% |
| Filtro regime tributário | 500-800ms | <50ms | 90% |
| Filtro por porte + cidade | 200-400ms | <30ms | 85% |
| Filtro CNAE + inativas | 700-1000ms | <100ms | 85% |
| Busca por texto (ILIKE) | 400-600ms | 300-400ms | 30% |

---

## 🔄 Manutenção Contínua

### Jobs Diários
```sql
-- Refresh de MVs (executar em horário de baixa utilização)
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_stats_cidade_situacao;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_regime_tributario_cidade;
```

### Jobs Semanais
```sql
-- Atualizar estatísticas das tabelas principais
ANALYZE estabelecimento;
ANALYZE empresa;
ANALYZE simples;

-- Verificar uso de índices
SELECT 
    schemaname, relname, indexrelname, idx_scan
FROM pg_stat_user_indexes 
WHERE schemaname = 'public'
ORDER BY idx_scan ASC
LIMIT 20;
```

### Jobs Mensais
```sql
-- Reindex de índices fragmentados
REINDEX INDEX CONCURRENTLY idx_estab_cidade_situacao_cnpj;

-- VACUUM FULL em horário de manutenção (se necessário)
VACUUM (VERBOSE) estabelecimento;
```

---

## 📝 Notas Importantes

1. **Execução CONCURRENTLY**: Todos os comandos `CREATE INDEX` usam `CONCURRENTLY` para não bloquear a tabela durante a criação.

2. **Espaço em Disco**: Os novos índices requerem aproximadamente 10-15 GB adicionais de espaço.

3. **Tempo de Criação**: Índices em tabelas grandes podem levar de 30 minutos a 2 horas para serem criados.

4. **Monitoramento**: Após implementação, monitorar `pg_stat_user_indexes` para validar uso dos novos índices.

5. **Rollback**: Se necessário, índices podem ser removidos com `DROP INDEX CONCURRENTLY`.

---

## 🏁 Conclusão

As otimizações propostas neste documento devem resolver os problemas de lentidão nos filtros de empresas inativas e nos filtros avançados. A implementação deve ser feita de forma gradual, começando pelos índices de alta prioridade que têm maior impacto imediato.

Após a implementação da Fase 1, espera-se que 90% dos problemas de performance sejam resolvidos. As fases subsequentes trazem melhorias incrementais e otimizações de manutenção.

---

## 🛠️ Implementação no ETL (Atualizado)

### Índices Adicionados em `advanced_indexes.py`

Os seguintes índices foram adicionados ao fluxo de ETL e serão criados automaticamente durante a execução de `python etl.py db index`:

#### Alta Prioridade (Implementados)

```python
# Suporta TODAS as situações cadastrais por cidade (não apenas ativas)
idx_estab_cidade_situacao_cnpj  # (cod_cidade_ibge, cod_situacao_cadastral, cnpj_completo)
idx_estab_estado_situacao_cnpj  # (cod_estado_ibge, cod_situacao_cadastral, cnpj_completo)

# Simples Nacional / MEI - Índices parciais
idx_simples_opcao_simples  # WHERE opcao_simples = 'S'
idx_simples_opcao_mei      # WHERE opcao_mei = 'S'
```

#### Média Prioridade (Implementados)

```python
# Filtros combinados cidade/estado + CNAE + situação
idx_estab_cidade_cnae_situacao  # (cod_cidade_ibge, cod_cnae_principal, cod_situacao_cadastral)
idx_estab_estado_cnae_situacao  # (cod_estado_ibge, cod_cnae_principal, cod_situacao_cadastral)

# JOINs otimizados com tabela empresa
idx_empresa_porte_cnpj      # (cod_porte, cnpj_basico)
idx_empresa_natureza_cnpj   # (cod_natureza_juridica, cnpj_basico)
```

#### Baixa Prioridade (Implementados)

```python
# DDD com covering index
idx_estab_ddd1_covering    # INCLUDE (cnpj_completo, cod_cidade_ibge)

# Busca textual em bairro
idx_estab_bairro_trgm      # GIN com pg_trgm

# Prospecção (emails sem contabilidade)
idx_estab_email_prospeccao # WHERE email NOT ILIKE '%contab%'
```

### Materialized Views Adicionadas

Três novas MVs foram criadas em `sql/materialized_views/`:

| Arquivo | MV | Descrição |
|---------|-----|-----------|
| `08_mv_stats_cidade_situacao.sql` | `mv_stats_cidade_situacao` | Contagens por cidade e situação cadastral |
| `09_mv_regime_tributario_cidade.sql` | `mv_regime_tributario_cidade` | Contagens por regime tributário (Simples/MEI) |
| `10_mv_porte_cidade.sql` | `mv_porte_cidade` | Contagens por porte da empresa |

### Schema Atualizado

A tabela `simples` foi atualizada em `schema.py`:
- Adicionada **Primary Key** em `cnpj_basico`
- Adicionado índice composto `idx_simples_opcoes` (opcao_simples, opcao_mei, cnpj_basico)

---

## 💡 Oportunidades Adicionais Identificadas

Além das melhorias do documento original, foram identificadas as seguintes oportunidades:

### 1. Índice para Região + Situação

```sql
-- Para filtros de região com diferentes situações
CREATE INDEX CONCURRENTLY idx_estab_regiao_situacao_cnpj
ON estabelecimento (cod_regiao_ibge, cod_situacao_cadastral, cnpj_completo);
```

**Impacto**: Filtros por região (Norte, Nordeste, etc.) com qualquer situação.

### 2. MV para Natureza Jurídica por Cidade

```sql
CREATE MATERIALIZED VIEW mv_natureza_cidade AS
SELECT 
    e.cod_cidade_ibge,
    emp.cod_natureza_juridica,
    e.cod_situacao_cadastral,
    COUNT(*) AS total
FROM estabelecimento e
JOIN empresa emp ON e.cnpj_basico = emp.cnpj_basico
GROUP BY e.cod_cidade_ibge, emp.cod_natureza_juridica, e.cod_situacao_cadastral;
```

**Impacto**: Filtros por natureza jurídica (EI, EIRELI, LTDA, SA, etc.).

### 3. Índice Covering para Listagem Básica

```sql
-- Evita acesso à tabela para listagens simples
CREATE INDEX CONCURRENTLY idx_estab_listagem_covering
ON estabelecimento (cod_cidade_ibge, cod_situacao_cadastral)
INCLUDE (cnpj_completo, nome_fantasia, cod_cnae_principal, data_inicio_atividade)
WHERE cod_situacao_cadastral = '02';
```

**Impacto**: Listagens de empresas ativas sem precisar acessar a heap.

### 4. Particionamento de Tabela (Futuro)

Para bases ainda maiores, considerar particionamento por:
- `cod_estado_ibge` (27 partições)
- `data_inicio_atividade` (por ano ou década)

**Benefícios**:
- Partition pruning automático
- Manutenção paralela (VACUUM, REINDEX)
- Arquivamento de dados antigos

---

## 📊 Estimativa de Espaço Adicional

| Categoria | Espaço Estimado |
|-----------|-----------------|
| Novos índices (alta prioridade) | ~5 GB |
| Novos índices (média prioridade) | ~4 GB |
| Novos índices (baixa prioridade) | ~2 GB |
| Novas MVs | ~3 GB |
| **Total** | **~14 GB** |

---

## 🚀 Comandos para Aplicar as Melhorias

```bash
# 1. Criar índices (inclui os novos)
python etl.py db index

# 2. Criar Materialized Views
python etl.py db views create

# 3. Atualizar estatísticas
psql -d companies_pgsql -c "ANALYZE estabelecimento; ANALYZE empresa; ANALYZE simples;"

# 4. (Opcional) Refresh das MVs
python etl.py db views refresh --concurrent
```

---

*Documento gerado em: Novembro 2024*  
*Revisão: 2.0 - Atualizado com implementação no ETL*

