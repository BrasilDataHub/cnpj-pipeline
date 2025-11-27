# Configuração e Personalização

## Constantes Globais

Todas as **constantes globais** como diretórios, downloads simultâneos, entre outras, podem ser ajustadas em
`src/rfb_cnpj_etl/config.py`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `DOWNLOAD_DIR` | Diretório de downloads | `data/downloads` |
| `DOWNLOAD_MAX_CONCURRENTS` | Downloads simultâneos | `10` |
| `POSTGRES` | Credenciais do PostgreSQL | `localhost:5432` |
| `BATCH_SIZE` | Tamanho do lote de inserção | `250000` |

## Variáveis de Ambiente

As configurações também podem ser definidas via variáveis de ambiente:

| Variável | Descrição |
|----------|-----------|
| `POSTGRES_HOST` | Host do PostgreSQL |
| `POSTGRES_PORT` | Porta do PostgreSQL |
| `POSTGRES_USER` | Usuário do PostgreSQL |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL |
| `POSTGRES_DBNAME` | Nome do banco de dados |
| `DOWNLOAD_PATH` | Diretório para downloads |
| `IBGE_CSV_DIR` | Diretório dos CSVs do IBGE |

## Chaves Primárias, Estrangeiras e Índices

As definições de chaves primárias, estrangeiras e índices podem ser encontradas em `src/rfb_cnpj_etl/db/schema.py`.
Edite conforme a sua necessidade.

---

## Scripts SQL Auxiliares (Opcionais)

Na pasta `sql/` estão disponíveis **scripts auxiliares** para otimizações avançadas. Esses scripts **não são executados
automaticamente** pelo ETL e devem ser aplicados manualmente conforme a necessidade do seu ambiente.

> **Nota:** Todos os índices (básicos e avançados como GIN, BRIN, HASH) já são criados automaticamente
> pelo comando `db index`. Os scripts abaixo oferecem otimizações adicionais para cenários específicos.

### Quando Utilizar

Execute esses scripts **após a conclusão do ETL** (após `db fk` ou `complete`), quando:
- Quiser estatísticas pré-calculadas para dashboards
- Necessitar de funções de manutenção e validação

### Scripts Disponíveis

| Arquivo | Propósito | Pré-requisitos |
|---------|-----------|----------------|
| `materialized_views.sql` | 6 views materializadas com estatísticas agregadas por estado, município, CNAE e período | Dados já carregados no banco |
| `general_improvements.sql` | Extensões PostgreSQL, funções de manutenção, validações e configurações de performance | Permissões de superusuário para algumas operações |

### Como Executar

```bash
# Conectar ao banco e executar (substitua as credenciais)
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/materialized_views.sql
psql -h localhost -U seu_usuario -d cnpj_rfb -f sql/general_improvements.sql
```

### Detalhes de Cada Script

**`materialized_views.sql`** - Estatísticas pré-calculadas:
- `mv_stats_estado`: empresas ativas por estado
- `mv_stats_municipio`: empresas ativas por município
- `mv_stats_cnae`: distribuição por atividade econômica
- `mv_stats_cnae_estado`: CNAEs por estado
- `mv_abertura_periodo`: aberturas por período
- `mv_top_cnaes_cidade`: principais CNAEs por cidade
- Função `refresh_all_mvs()` para atualização

**`general_improvements.sql`** - Configurações e manutenção:
- Extensões: `pg_trgm`, `pg_prewarm`, `pg_stat_statements`
- Funções: `prewarm_critical_indexes()`, `vacuum_analyze_all()`, `table_statistics()`
- Validações: `validate_cnpj_completo()`, `check_referential_integrity()`

