# Exemplos de Consultas PostgreSQL

Este documento contém exemplos práticos de consultas SQL para explorar a base de dados CNPJ.

## Estrutura do Banco de Dados

### Tabelas Principais

| Tabela | Descrição | PK |
|--------|-----------|-----|
| `empresa` | Dados da empresa (razão social, natureza, porte, capital) | `cnpj_basico` |
| `estabelecimento` | Estabelecimentos (matriz/filiais, endereço, contatos) | `cnpj_completo` |
| `socio` | Quadro societário | - |
| `simples` | Opção pelo Simples Nacional e MEI | - |

### Tabelas de Domínio (Lookup)

| Tabela | Descrição | PK |
|--------|-----------|-----|
| `cnae` | Classificação Nacional de Atividades Econômicas | `cod_cnae` |
| `natureza_juridica` | Naturezas jurídicas | `cod_natureza` |
| `qualificacao_socio` | Qualificações de sócios | `cod_qualificacao` |
| `motivo` | Motivos de situação cadastral | `cod_motivo` |
| `pais` | Países | `cod_pais` |
| `municipio_rfb` | Municípios (código RFB) | `cod_municipio` |

### Tabelas IBGE (Enriquecimento)

| Tabela | Descrição | PK |
|--------|-----------|-----|
| `ibge_regiao` | Regiões do Brasil | `cod_regiao_ibge` |
| `ibge_estado` | Estados com coordenadas | `cod_estado_ibge` |
| `ibge_cidade` | Cidades com coordenadas, DDD, fuso | `cod_cidade_ibge` |

### Tabela de CNAEs Secundários

| Tabela | Descrição | FK |
|--------|-----------|-----|
| `estabelecimento_cnae_sec` | CNAEs secundários por estabelecimento | `cnpj_completo` → `estabelecimento` |

---

## Consultas Básicas

### Buscar empresa por CNPJ completo

```sql
-- Usando cnpj_completo (PK da tabela estabelecimento)
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

### Buscar empresa por CNPJ parcial (apenas 8 dígitos iniciais)

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

### Estabelecimentos por estado (usando tabela IBGE)

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
-- Usando cnpj_completo (nova estrutura simplificada)
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
WHERE est.cod_estado_ibge = 35  -- São Paulo
  AND est.cod_situacao_cadastral = '02'
GROUP BY cn.cod_cnae, cn.nome_cnae
ORDER BY total DESC
LIMIT 20;
```

### Estabelecimentos com CNAE secundário específico

```sql
-- Usando as colunas desnormalizadas para filtrar sem JOIN
SELECT 
    sec.cnpj_completo,
    sec.cod_cnae,
    cn.nome_cnae,
    uf.sigla_uf
FROM estabelecimento_cnae_sec sec
JOIN cnae cn ON sec.cod_cnae = cn.cod_cnae
JOIN ibge_estado uf ON sec.cod_estado_ibge = uf.cod_estado_ibge
WHERE sec.cod_cnae = '6201501'  -- Desenvolvimento de software
  AND sec.cod_estado_ibge = 35  -- São Paulo
LIMIT 100;
```

---

## Consulta Completa de Estabelecimento

Esta consulta retorna informações detalhadas de estabelecimentos, incluindo empresa, endereço, 
sócios, CNAEs secundários e opção pelo Simples Nacional.

```sql
SELECT
    est.cnpj_completo AS "CNPJ",
    e.razao_social AS "Razão Social",
    CASE est.matriz_filial 
        WHEN '1' THEN 'MATRIZ' 
        WHEN '2' THEN 'FILIAL' 
    END AS "Tipo",
    est.nome_fantasia AS "Nome Fantasia",
    TO_CHAR(est.data_inicio_atividade, 'DD/MM/YYYY') AS "Data Abertura",
    CASE est.cod_situacao_cadastral 
        WHEN '01' THEN 'NULA' 
        WHEN '02' THEN 'ATIVA' 
        WHEN '03' THEN 'SUSPENSA' 
        WHEN '04' THEN 'INAPTA' 
        WHEN '08' THEN 'BAIXADA' 
    END AS "Situação",
    nat.nome_natureza AS "Natureza Jurídica",
    CASE e.cod_porte 
        WHEN '00' THEN 'Não Informado' 
        WHEN '01' THEN 'Microempresa' 
        WHEN '03' THEN 'Pequeno Porte' 
        WHEN '05' THEN 'Demais' 
    END AS "Porte",
    TO_CHAR(e.capital_social, 'FM999,999,999,990.00') AS "Capital Social",
    cn.cod_cnae || ' - ' || cn.nome_cnae AS "CNAE Principal",
    -- CNAEs Secundários
    (
        SELECT STRING_AGG(sec.cod_cnae || ' - ' || cn_sec.nome_cnae, ' | ')
        FROM estabelecimento_cnae_sec sec
        JOIN cnae cn_sec ON sec.cod_cnae = cn_sec.cod_cnae
        WHERE sec.cnpj_completo = est.cnpj_completo
    ) AS "CNAEs Secundários",
    -- Endereço
    CONCAT_WS(' ', est.tipo_logradouro, est.logradouro, est.numero, est.complemento) AS "Endereço",
    est.bairro AS "Bairro",
    cid.nome_cidade AS "Cidade",
    uf.sigla_uf AS "UF",
    reg.nome_regiao AS "Região",
    est.cep AS "CEP",
    -- Contatos
    CASE WHEN est.telefone_1 IS NOT NULL 
        THEN '(' || est.ddd_telefone_1 || ') ' || est.telefone_1 
    END AS "Telefone",
    est.email AS "E-mail",
    -- Simples Nacional
    CASE sn.opcao_simples WHEN 'S' THEN 'Sim' ELSE 'Não' END AS "Optante Simples",
    CASE sn.opcao_mei WHEN 'S' THEN 'Sim' ELSE 'Não' END AS "MEI",
    -- Sócios
    (
        SELECT STRING_AGG(UPPER(s.nome_socio), ', ')
        FROM socio s
        WHERE s.cnpj_basico = e.cnpj_basico
    ) AS "Sócios"
FROM estabelecimento est
JOIN empresa e ON est.cnpj_basico = e.cnpj_basico
LEFT JOIN simples sn ON e.cnpj_basico = sn.cnpj_basico
LEFT JOIN cnae cn ON est.cod_cnae_principal = cn.cod_cnae
LEFT JOIN natureza_juridica nat ON e.cod_natureza_juridica = nat.cod_natureza
LEFT JOIN ibge_cidade cid ON est.cod_cidade_ibge = cid.cod_cidade_ibge
LEFT JOIN ibge_estado uf ON est.cod_estado_ibge = uf.cod_estado_ibge
LEFT JOIN ibge_regiao reg ON est.cod_regiao_ibge = reg.cod_regiao_ibge
WHERE
    uf.sigla_uf = 'SP'
    AND est.cod_situacao_cadastral = '02'
    AND est.data_inicio_atividade >= '2024-01-01'
ORDER BY e.razao_social
LIMIT 20;
```

---

## Consultas de Estatísticas

### Aberturas por mês

```sql
SELECT 
    DATE_TRUNC('month', est.data_inicio_atividade) AS mes,
    COUNT(*) AS aberturas
FROM estabelecimento est
WHERE est.data_inicio_atividade >= '2024-01-01'
  AND est.matriz_filial = '1'  -- Apenas matrizes
GROUP BY DATE_TRUNC('month', est.data_inicio_atividade)
ORDER BY mes;
```

### Top 10 CNAEs por cidade

```sql
SELECT 
    cid.nome_cidade,
    uf.sigla_uf,
    cn.cod_cnae,
    cn.nome_cnae,
    COUNT(*) AS total
FROM estabelecimento est
JOIN ibge_cidade cid ON est.cod_cidade_ibge = cid.cod_cidade_ibge
JOIN ibge_estado uf ON cid.cod_estado_ibge = uf.cod_estado_ibge
JOIN cnae cn ON est.cod_cnae_principal = cn.cod_cnae
WHERE est.cod_situacao_cadastral = '02'
  AND cid.nome_cidade = 'São Paulo'
GROUP BY cid.nome_cidade, uf.sigla_uf, cn.cod_cnae, cn.nome_cnae
ORDER BY total DESC
LIMIT 10;
```

### Empresas optantes pelo Simples por estado

```sql
SELECT 
    uf.sigla_uf,
    COUNT(DISTINCT e.cnpj_basico) AS total_empresas,
    COUNT(DISTINCT e.cnpj_basico) FILTER (WHERE sn.opcao_simples = 'S') AS optantes_simples,
    COUNT(DISTINCT e.cnpj_basico) FILTER (WHERE sn.opcao_mei = 'S') AS meis,
    ROUND(
        100.0 * COUNT(DISTINCT e.cnpj_basico) FILTER (WHERE sn.opcao_simples = 'S') / 
        NULLIF(COUNT(DISTINCT e.cnpj_basico), 0), 2
    ) AS pct_simples
FROM estabelecimento est
JOIN empresa e ON est.cnpj_basico = e.cnpj_basico
LEFT JOIN simples sn ON e.cnpj_basico = sn.cnpj_basico
JOIN ibge_estado uf ON est.cod_estado_ibge = uf.cod_estado_ibge
WHERE est.matriz_filial = '1'
  AND est.cod_situacao_cadastral = '02'
GROUP BY uf.sigla_uf
ORDER BY total_empresas DESC;
```

---

## Dicas de Performance

### Índices Disponíveis

Os seguintes índices são criados automaticamente pelo ETL:

| Tabela | Índice | Colunas |
|--------|--------|---------|
| `estabelecimento` | PK | `cnpj_completo` |
| `estabelecimento` | `idx_estab_empresa` | `cnpj_basico` |
| `estabelecimento` | `idx_estab_uf_municipio` | `uf, cod_municipio` |
| `estabelecimento` | `idx_estab_cnae_principal` | `cod_cnae_principal` |
| `estabelecimento` | `idx_estab_situacao` | `cod_situacao_cadastral` |
| `estabelecimento` | `idx_estab_cidade_ibge` | `cod_cidade_ibge` |
| `estabelecimento` | `idx_estab_estado_ibge` | `cod_estado_ibge` |
| `estabelecimento_cnae_sec` | `idx_estab_cnae_sec_cnpj` | `cnpj_completo` |
| `socio` | `idx_socio_empresa` | `cnpj_basico` |
| `socio` | `idx_socio_cpf_cnpj` | `cnpj_cpf_socio` |

### Boas Práticas

1. **Use `cnpj_completo` para buscas exatas** - É a PK e tem índice automático
2. **Filtre por `cod_situacao_cadastral`** - Reduz drasticamente o volume de dados
3. **Use códigos IBGE para localização** - `cod_estado_ibge` e `cod_cidade_ibge` têm índices
4. **Evite `LIKE '%termo%'** - Para buscas textuais, execute `sql/indexes.sql` para criar índices GIN
5. **Use as Views Materializadas** - Para estatísticas agregadas, execute `sql/materialized_views.sql`

