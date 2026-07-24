# db/postgres_builder.py

"""
Módulo para construção do banco de dados PostgreSQL.
"""

import os
import time
import psycopg2
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from ..db.schema import SCHEMA
from ..db.advanced_indexes import (
    ADVANCED_INDEXES,
    REQUIRED_EXTENSIONS,
    INDEX_CREATION_CONFIG,
    build_create_index_sql,
    get_indexes_by_table
)
from ..utils.db_patch import apply_static_fixes
from ..utils.logger import print_log


class PostgresBuilder:
    """
    Classe para construção do banco de dados PostgreSQL.
    """

    def __init__(self, config):
        self.config = config
        self.conn = None

    def _connect(self):
        """Abre a conexão com o Postgres."""
        try:
            conn = psycopg2.connect(
                host=self.config["host"],
                port=self.config["port"],
                database=self.config["database"],
                user=self.config["user"],
                password=self.config["password"],
            )
            return conn
        except psycopg2.Error as e:
            print_log(f"ERRO AO CONECTAR NO BANCO: {e}", level="error")
            raise

    def _create_database(self):
        try:
            temp_config = self.config.copy()
            temp_config["database"] = "postgres"
            conn = psycopg2.connect(**temp_config)
            conn.autocommit = True
            cur = conn.cursor()
            db_name = self.config['database']
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (db_name,))
            exists = cur.fetchone()
            if not exists:
                cur.execute(f"CREATE DATABASE {db_name}")
                print_log("BANCO CRIADO", level="success")
            conn.close()
        except psycopg2.Error as e:
            print_log(f"ERRO AO CRIAR BANCO: {e}", level="error")
            raise

    def enable_extensions(self):
        """
        Habilita extensões necessárias para índices avançados e operação.
        Extensões habilitadas: pg_trgm (busca textual com trigrams),
        unaccent (normalização de acentos) e pg_stat_statements
        (diagnóstico de queries; coleta ativa exige preload na instância).
        """
        try:
            print_log("HABILITANDO EXTENSÕES...", level="task")
            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()

            for ext in REQUIRED_EXTENSIONS:
                cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext};")
                print_log(f"  -> Extensão '{ext}' habilitada", level="docs")

            print_log("EXTENSÕES HABILITADAS", level="success")
        except psycopg2.Error as e:
            print_log(f"ERRO AO HABILITAR EXTENSÕES: {e}", level="error")
            raise

    def drop_tables(self):
        try:
            conn = self._connect()
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
            for (table_name,) in cur.fetchall():
                cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
            conn.close()
            print_log("TABELAS ANTERIORES REMOVIDAS", level="docs")
        except psycopg2.Error as e:
            print_log(f"ERRO AO EXCLUIR TABELAS: {e}", level="error")
            raise

    def create_tables(self):
        """
        Cria todas as tabelas. As chaves primárias definidas inline nas colunas
        (em tabelas menores) são criadas aqui. As PKs de tabelas maiores,
        definidas separadamente no SCHEMA, são adiadas.
        """
        try:
            print_log("CRIANDO TABELAS...", level="task")
            if self.conn is None: self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()

            for table_name, definition in SCHEMA.items():
                columns_sql = [
                    f'"{col_name}" {col_type}'
                    for col_name, col_type in definition['columns']
                ]
                columns_str = ", ".join(columns_sql)

                cur.execute(f'CREATE UNLOGGED TABLE IF NOT EXISTS public."{table_name}" ({columns_str});')

            print_log("TABELAS CRIADAS", level="success")
        except psycopg2.Error as e:
            print_log(f"ERRO AO CRIAR TABELAS: {e}", level="error")
            raise

    def _add_primary_keys(self):
        """
        Adiciona as chaves primárias APENAS para as tabelas que as definem
        separadamente no SCHEMA (ex: 'empresa', 'estabelecimento').
        """
        print_log("ADICIONANDO CHAVES PRIMÁRIAS (TABELAS GRANDES)...", level="task")
        if self.conn is None: self.conn = self._connect()
        self.conn.autocommit = True
        cur = self.conn.cursor()

        for table_name, definition in SCHEMA.items():
            pk_cols = []
            if 'primary_key' in definition:
                pk_cols = definition['primary_key']

            if pk_cols:
                pk_cols_str = ', '.join(f'"{col}"' for col in pk_cols)
                sql_command = f'ALTER TABLE public."{table_name}" ADD PRIMARY KEY ({pk_cols_str});'

                try:
                    print_log(f"  -> Adicionando PK em '{table_name}'...", level="docs")
                    cur.execute(sql_command)
                except psycopg2.Error as e:
                    # 42P16 = multiple_primary_keys, 42P07 = relation_already_exists
                    if e.pgcode in ('42P16', '42P07'):
                        print_log(f"     ℹ PK em '{table_name}' já existe, pulando.", level="docs")
                    else:
                        print_log(f"ERRO AO ADICIONAR PK em '{table_name}': {e}", level="error")
                        raise

        print_log("CHAVES PRIMÁRIAS (TABELAS GRANDES) ADICIONADAS", level="success")

    def apply_patches(self):
        """
        Aplica correções estáticas nos dados.
        Pode ser executado independentemente via: python etl.py db patch
        """
        if self.conn is None:
            self.conn = self._connect()
        apply_static_fixes(self.conn)

    def add_primary_keys(self):
        """
        Adiciona chaves primárias nas tabelas grandes.
        Pode ser executado independentemente via: python etl.py db pk
        """
        self._add_primary_keys()

    def patch_data(self):
        """
        [LEGADO] Aplica correções estáticas + adiciona PKs.
        Mantido para compatibilidade. Prefira usar apply_patches() e add_primary_keys() separadamente.
        """
        self.apply_patches()
        self.add_primary_keys()

    def set_tables_logged(self):
        """
        Converte as tabelas de UNLOGGED para LOGGED ao final da carga.

        O ETL cria tudo UNLOGGED para acelerar o COPY (sem WAL), mas tabela
        UNLOGGED não sobrevive a crash: o PostgreSQL TRUNCA o conteúdo no
        recovery — um simples crash da instância perderia a base inteira.
        LOGGED também é pré-condição para backup físico/PITR e réplicas.

        Custo: reescrita completa com WAL, tabela a tabela (estimativa de
        +1–3 h nas 5 tabelas grandes; `max_wal_size` alto reduz checkpoints).
        A conversão é feita logo após a carga/patches e antes de PKs e
        índices — com menos relações existindo, menos bytes são reescritos.

        Idempotente: consulta relpersistence e só converte o que ainda é 'u'.

        Ordem: dependência de FK, não tamanho — o PostgreSQL recusa converter
        uma tabela para LOGGED enquanto ela referenciar (FK) uma tabela ainda
        UNLOGGED. No pipeline as FKs ainda não existem nesta etapa e qualquer
        ordem serve; no uso avulso (`db logged` contra uma base já com FKs,
        como a produção) a ordem errada aborta com "could not change table
        ... because it references unlogged table". Dentro de cada nível de
        dependência, converte da menor para a maior (falha cedo se faltar
        espaço/WAL).

        Pode ser executado independentemente via: python etl.py db logged
        """
        try:
            print_log("CONVERTENDO TABELAS PARA LOGGED...", level="task")
            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()

            cur.execute("""
                SELECT relname, pg_total_relation_size(c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relpersistence = 'u';
            """)
            sizes = {row[0]: row[1] for row in cur.fetchall()}
            remaining = set(sizes)

            if not remaining:
                print_log("NENHUMA TABELA UNLOGGED ENCONTRADA", level="docs")
                return

            # Arestas de FK entre tabelas ainda UNLOGGED: quem referencia só
            # pode ser convertida depois da referenciada.
            cur.execute("""
                SELECT rel.relname, ref.relname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_class ref ON ref.oid = con.confrelid
                JOIN pg_namespace n ON n.oid = rel.relnamespace
                WHERE con.contype = 'f' AND n.nspname = 'public';
            """)
            depends_on: dict[str, set] = {}
            for table_name, referenced in cur.fetchall():
                if table_name in remaining and referenced in remaining and table_name != referenced:
                    depends_on.setdefault(table_name, set()).add(referenced)

            total = len(remaining)
            width = len(str(total))
            converted: set = set()
            i = 0

            while remaining:
                ready = [
                    t for t in remaining
                    if not (depends_on.get(t, set()) - converted)
                ]

                if not ready:
                    raise RuntimeError(
                        "Ciclo de FKs entre tabelas UNLOGGED impede a conversão: "
                        + ", ".join(sorted(remaining))
                    )

                for table_name in sorted(ready, key=lambda t: sizes[t]):
                    i += 1
                    start_time = time.time()
                    cur.execute(f'ALTER TABLE public."{table_name}" SET LOGGED;')
                    elapsed = time.time() - start_time
                    print_log(
                        f"[{i:0{width}}/{total}] LOGGED: {table_name} ({elapsed:.1f}s)",
                        level="docs"
                    )
                    converted.add(table_name)
                    remaining.discard(table_name)

            print_log("TABELAS CONVERTIDAS PARA LOGGED", level="success")
        except (psycopg2.Error, RuntimeError) as e:
            print_log(f"ERRO AO CONVERTER TABELAS PARA LOGGED: {e}", level="error")
            raise

    def enable_foreign_keys(self):
        """Cria as chaves estrangeiras definidas no SCHEMA."""
        try:
            print_log("CRIANDO CHAVES ESTRANGEIRAS...", level="task")
            if self.conn is None: self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()

            # 1. Coleta todas as definições de FKs para obter um total
            all_fks = []
            for table_name, definition in SCHEMA.items():
                if 'foreign_keys' in definition:
                    for i, fk_def in enumerate(definition['foreign_keys'], start=1):
                        all_fks.append((table_name, fk_def, i))

            # 2. Itera sobre a lista de FKs com um contador global
            total = len(all_fks)
            width = len(str(total))
            for i, (table_name, fk, fk_index) in enumerate(all_fks, start=1):
                constraint_name = f"fk_{table_name}_{fk_index}"
                try:
                    fk_columns = ', '.join(f'"{col}"' for col in fk['columns'])
                    ref_table_and_cols = fk['references']
                    ref_table = ref_table_and_cols.split('(')[0].strip()
                    ref_cols_str = ref_table_and_cols.split('(')[1].replace(')', '')
                    ref_cols = ', '.join(f'"{c.strip()}"' for c in ref_cols_str.split(','))

                    fk_sql = (f'ALTER TABLE public."{table_name}" '
                              f'ADD CONSTRAINT "{constraint_name}" '
                              f'FOREIGN KEY ({fk_columns}) '
                              f'REFERENCES public."{ref_table}"({ref_cols});')

                    cur.execute(fk_sql)
                    print_log(f"[{i:0{width}}/{total}] FK CRIADA: {constraint_name} em '{table_name}'", level="docs")

                except psycopg2.Error as e:
                    # 42710 = duplicate_object (constraint já existe)
                    if e.pgcode == '42710':
                        print_log(f"[{i:0{width}}/{total}] FK JÁ EXISTE: {constraint_name}, pulando.", level="docs")
                    else:
                        print_log(f"[{i:0{width}}/{total}] ERRO AO ADICIONAR FK '{constraint_name}': {e}",
                                  level="error")

            print_log("CHAVES ESTRANGEIRAS CRIADAS", level="success")
        except psycopg2.Error as e:
            print_log(f"ERRO AO CRIAR FKs: {e}", level="error")
            raise

    def _create_indexes_for_table(self, table_name: str, indexes: list, 
                                    progress_lock: Lock, progress_counter: dict, 
                                    total: int) -> list:
        """
        Cria todos os índices de uma tabela SEQUENCIALMENTE usando uma conexão dedicada.
        Isso evita deadlocks que ocorrem quando múltiplas conexões tentam criar
        índices na mesma tabela com CREATE INDEX CONCURRENTLY.
        
        Returns:
            list: Lista de tuplas (index_name, success: bool, error_msg: str or None)
        """
        results = []
        conn = None
        
        try:
            # Uma conexão por tabela
            conn = self._connect()
            conn.autocommit = True
            cur = conn.cursor()
            
            for index in indexes:
                index_name = index.get('name', 'desconhecido')
                try:
                    index_cols = ', '.join(f'"{col}"' for col in index['columns'])
                    # Usa CREATE INDEX CONCURRENTLY para não bloquear outras operações
                    stmt = f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" ON public."{table_name}" ({index_cols});'
                    cur.execute(stmt)
                    
                    # Atualiza progresso de forma thread-safe
                    with progress_lock:
                        progress_counter['count'] += 1
                        current = progress_counter['count']
                        width = len(str(total))
                        print_log(f"[{current:0{width}}/{total}] ÍNDICE CRIADO: {index_name}", level="docs")
                    
                    results.append((index_name, True, None))
                    
                except (psycopg2.Error, KeyError) as e:
                    with progress_lock:
                        progress_counter['count'] += 1
                    results.append((index_name, False, str(e)))
            
            cur.close()
            
        except Exception as e:
            # Se falhar ao conectar, marcar todos os índices como falha
            for index in indexes:
                index_name = index.get('name', 'desconhecido')
                with progress_lock:
                    progress_counter['count'] += 1
                results.append((index_name, False, str(e)))
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        
        return results

    def create_indexes(self, parallel: bool = True, max_workers: int = 4):
        """
        Cria todos os índices: básicos (definidos no SCHEMA) e avançados (GIN, BRIN, HASH).
        
        Esta função executa duas etapas:
        1. Índices básicos: BTREE simples para JOINs, FKs e consultas comuns
        2. Índices avançados: GIN (busca textual), BRIN (datas), HASH (lookups), parciais e compostos
        
        Args:
            parallel: Se True, cria índices em paralelo usando múltiplas conexões.
            max_workers: Número máximo de workers para criação paralela.
        """
        try:
            # Etapa 1: Índices básicos (SCHEMA)
            all_indexes = []
            for table_name, definition in SCHEMA.items():
                if 'indexes' in definition:
                    for index in definition['indexes']:
                        all_indexes.append((table_name, index))

            total = len(all_indexes)
            
            if parallel and total > 1:
                self._create_indexes_parallel(all_indexes, max_workers)
            else:
                self._create_indexes_sequential(all_indexes)
            
            # Etapa 2: Índices avançados (GIN, BRIN, HASH, parciais, compostos)
            self.create_advanced_indexes(parallel=parallel, max_workers=max_workers)
                
        except Exception as e:
            print_log(f"ERRO AO CRIAR ÍNDICES: {e}", level="error")
            raise

    def _create_indexes_parallel(self, all_indexes: list, max_workers: int = 4):
        """
        Cria índices em paralelo usando ThreadPoolExecutor.
        
        IMPORTANTE: Para evitar deadlocks, índices da MESMA TABELA são criados
        sequencialmente, mas índices de TABELAS DIFERENTES são criados em paralelo.
        
        Isso porque CREATE INDEX CONCURRENTLY requer locks que podem conflitar
        quando múltiplas conexões tentam criar índices na mesma tabela.
        """
        total = len(all_indexes)
        
        # Agrupar índices por tabela
        indexes_by_table = {}
        for table_name, index in all_indexes:
            if table_name not in indexes_by_table:
                indexes_by_table[table_name] = []
            indexes_by_table[table_name].append(index)
        
        num_tables = len(indexes_by_table)
        effective_workers = min(max_workers, num_tables)
        
        print_log(f"CRIANDO {total} ÍNDICES ({num_tables} tabelas, workers={effective_workers})...", level="task")
        
        progress_lock = Lock()
        progress_counter = {'count': 0}
        errors = []
        
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            # Submete uma tarefa por TABELA (não por índice)
            # Cada tarefa cria todos os índices da tabela sequencialmente
            futures = {
                executor.submit(
                    self._create_indexes_for_table,
                    table_name,
                    indexes,
                    progress_lock, 
                    progress_counter, 
                    total
                ): table_name
                for table_name, indexes in indexes_by_table.items()
            }
            
            for future in as_completed(futures):
                results = future.result()
                for index_name, success, error_msg in results:
                    if not success:
                        errors.append(f"{index_name}: {error_msg}")
        
        if errors:
            print_log(f"ÍNDICES CRIADOS COM {len(errors)} ERRO(S):", level="warning")
            for err in errors[:5]:  # Mostra no máximo 5 erros
                print_log(f"  -> {err}", level="error")
        else:
            print_log("TODOS OS ÍNDICES FORAM CRIADOS COM SUCESSO", level="success")

    def _create_indexes_sequential(self, all_indexes: list):
        """
        Cria índices sequencialmente (fallback para quando paralelo não é desejado).
        """
        total = len(all_indexes)
        print_log(f"CRIANDO {total} ÍNDICES (modo sequencial)...", level="task")
        
        if self.conn is None: 
            self.conn = self._connect()
        self.conn.autocommit = True
        cur = self.conn.cursor()

        width = len(str(total))
        for i, (table_name, index) in enumerate(all_indexes, start=1):
            index_name = "desconhecido"
            try:
                index_name = index['name']
                index_cols = ', '.join(f'"{col}"' for col in index['columns'])
                stmt = f'CREATE INDEX IF NOT EXISTS "{index_name}" ON public."{table_name}" ({index_cols});'
                cur.execute(stmt)
                print_log(f"[{i:0{width}}/{total}] ÍNDICE CRIADO: {index_name}", level="docs")
            except (psycopg2.Error, KeyError) as e:
                print_log(f"Erro ao criar índice {index_name}: {e}", level="error")

        print_log("TODOS OS ÍNDICES FORAM CRIADOS", level="success")

    def _create_advanced_indexes_for_table(self, table_name: str, indexes: list,
                                            progress_lock: Lock, progress_counter: dict,
                                            total: int) -> list:
        """
        Cria todos os índices avançados de uma tabela SEQUENCIALMENTE usando uma conexão dedicada.
        Isso evita deadlocks que ocorrem quando múltiplas conexões tentam criar
        índices na mesma tabela com CREATE INDEX CONCURRENTLY.
        
        Returns:
            list: Lista de tuplas (index_name, success: bool, error_msg: str or None)
        """
        results = []
        conn = None
        
        try:
            # Uma conexão por tabela
            conn = self._connect()
            conn.autocommit = True
            cur = conn.cursor()
            
            for index_def in indexes:
                index_name = index_def.get('name', 'desconhecido')
                try:
                    sql = build_create_index_sql(index_def, concurrent=True)
                    cur.execute(sql)
                    
                    # Atualiza progresso de forma thread-safe
                    with progress_lock:
                        progress_counter['count'] += 1
                        current = progress_counter['count']
                        width = len(str(total))
                        index_type = index_def.get('type', 'BTREE')
                        print_log(f"[{current:0{width}}/{total}] ÍNDICE {index_type} CRIADO: {index_name}", level="docs")
                    
                    results.append((index_name, True, None))
                    
                except psycopg2.Error as e:
                    with progress_lock:
                        progress_counter['count'] += 1
                    results.append((index_name, False, str(e)))
            
            cur.close()
            
        except Exception as e:
            # Se falhar ao conectar, marcar todos os índices restantes como falha
            for index_def in indexes:
                index_name = index_def.get('name', 'desconhecido')
                if not any(r[0] == index_name for r in results):
                    with progress_lock:
                        progress_counter['count'] += 1
                    results.append((index_name, False, str(e)))
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        
        return results

    def create_advanced_indexes(self, parallel: bool = True, max_workers: int = 4):
        """
        Cria índices avançados para otimização de consultas.
        
        Estes índices incluem:
        - GIN (pg_trgm): Para busca textual com ILIKE '%termo%'
        - BRIN: Para colunas de data (economia de espaço)
        - HASH: Para lookups exatos de alta performance
        - Índices parciais: Para subconjuntos frequentemente consultados
        - Índices compostos: Para consultas específicas de negócio
        
        Args:
            parallel: Se True, cria índices em paralelo usando múltiplas conexões.
            max_workers: Número máximo de workers para criação paralela.
        """
        total = len(ADVANCED_INDEXES)
        
        if total == 0:
            print_log("NENHUM ÍNDICE AVANÇADO DEFINIDO", level="warning")
            return
        
        # Configurar parâmetros de performance
        try:
            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()
            
            for param, value in INDEX_CREATION_CONFIG.items():
                cur.execute(f"SET {param} = '{value}';")
                print_log(f"  -> {param} = {value}", level="docs")
                
        except psycopg2.Error as e:
            print_log(f"AVISO: Não foi possível configurar parâmetros de performance: {e}", level="warning")
        
        if parallel and total > 1:
            self._create_advanced_indexes_parallel(max_workers)
        else:
            self._create_advanced_indexes_sequential()

    def _create_advanced_indexes_parallel(self, max_workers: int = 4):
        """
        Cria índices avançados em paralelo usando ThreadPoolExecutor.
        
        IMPORTANTE: Para evitar deadlocks, índices da MESMA TABELA são criados
        sequencialmente, mas índices de TABELAS DIFERENTES são criados em paralelo.
        
        Isso porque CREATE INDEX CONCURRENTLY requer locks que podem conflitar
        quando múltiplas conexões tentam criar índices na mesma tabela.
        """
        total = len(ADVANCED_INDEXES)
        indexes_by_table = get_indexes_by_table()
        num_tables = len(indexes_by_table)
        effective_workers = min(max_workers, num_tables)
        
        print_log(f"CRIANDO {total} ÍNDICES AVANÇADOS ({num_tables} tabelas, workers={effective_workers})...", level="task")
        
        progress_lock = Lock()
        progress_counter = {'count': 0}
        errors = []
        
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            # Submete uma tarefa por TABELA (não por índice)
            # Cada tarefa cria todos os índices da tabela sequencialmente
            futures = {
                executor.submit(
                    self._create_advanced_indexes_for_table,
                    table_name,
                    indexes,
                    progress_lock,
                    progress_counter,
                    total
                ): table_name
                for table_name, indexes in indexes_by_table.items()
            }
            
            for future in as_completed(futures):
                results = future.result()
                for index_name, success, error_msg in results:
                    if not success:
                        errors.append(f"{index_name}: {error_msg}")
        
        if errors:
            print_log(f"ÍNDICES AVANÇADOS CRIADOS COM {len(errors)} ERRO(S):", level="warning")
            for err in errors[:5]:
                print_log(f"  -> {err}", level="error")
        else:
            print_log("TODOS OS ÍNDICES AVANÇADOS FORAM CRIADOS COM SUCESSO", level="success")

    def _create_advanced_indexes_sequential(self):
        """
        Cria índices avançados sequencialmente.
        """
        total = len(ADVANCED_INDEXES)
        print_log(f"CRIANDO {total} ÍNDICES AVANÇADOS (modo sequencial)...", level="task")
        
        if self.conn is None:
            self.conn = self._connect()
        self.conn.autocommit = True
        cur = self.conn.cursor()
        
        width = len(str(total))
        errors = []
        
        for i, index_def in enumerate(ADVANCED_INDEXES, start=1):
            index_name = index_def.get('name', 'desconhecido')
            try:
                # Modo sequencial não usa CONCURRENTLY para maior compatibilidade
                sql = build_create_index_sql(index_def, concurrent=False)
                cur.execute(sql)
                index_type = index_def.get('type', 'BTREE')
                print_log(f"[{i:0{width}}/{total}] ÍNDICE {index_type} CRIADO: {index_name}", level="docs")
            except psycopg2.Error as e:
                errors.append(f"{index_name}: {e}")
                print_log(f"[{i:0{width}}/{total}] ERRO: {index_name} - {e}", level="error")
        
        if errors:
            print_log(f"ÍNDICES AVANÇADOS CRIADOS COM {len(errors)} ERRO(S)", level="warning")
        else:
            print_log("TODOS OS ÍNDICES AVANÇADOS FORAM CRIADOS", level="success")

    def initialize_schema(self) -> None:
        """Executa o fluxo completo de criação do schema."""
        try:
            self._create_database()
            self.conn = self._connect()
            self.enable_extensions()
            self.drop_tables()
            self.create_tables()
        finally:
            if self.conn:
                self.conn.close()
                self.conn = None

    def _get_mv_sql_dir(self) -> Path:
        """Retorna o caminho da pasta sql/materialized_views/."""
        # Navega de db/ -> rfb_cnpj_etl/ -> src/ -> projeto/ -> sql/materialized_views/
        base_dir = Path(__file__).resolve().parents[3]
        return base_dir / "sql" / "materialized_views"

    def create_materialized_views(self) -> None:
        """
        Cria todas as Materialized Views a partir dos arquivos SQL.
        
        Os arquivos SQL são lidos da pasta sql/materialized_views/ e executados
        sequencialmente na ordem alfabética (numeração no nome do arquivo).
        
        Pode ser executado via: python etl.py db views create
        """
        try:
            print_log("CRIANDO MATERIALIZED VIEWS...", level="task")
            
            sql_dir = self._get_mv_sql_dir()
            if not sql_dir.exists():
                raise FileNotFoundError(f"Pasta não encontrada: {sql_dir}")
            
            # Lista arquivos .sql ordenados por nome
            sql_files = sorted(sql_dir.glob("*.sql"))
            
            if not sql_files:
                print_log("NENHUM ARQUIVO SQL ENCONTRADO", level="warning")
                return
            
            total = len(sql_files)
            width = len(str(total))
            
            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()
            
            for i, sql_file in enumerate(sql_files, start=1):
                file_name = sql_file.name
                try:
                    print_log(f"[{i:0{width}}/{total}] EXECUTANDO: {file_name}...", level="docs")
                    
                    sql_content = sql_file.read_text(encoding="utf-8")
                    
                    # Remove comentários de linha única e executa
                    # O psycopg2 aceita múltiplos statements separados por ;
                    start_time = time.time()
                    cur.execute(sql_content)
                    elapsed = time.time() - start_time
                    
                    print_log(f"[{i:0{width}}/{total}] CONCLUÍDO: {file_name} ({elapsed:.1f}s)", level="success")
                    
                except psycopg2.Error as e:
                    print_log(f"[{i:0{width}}/{total}] ERRO EM {file_name}: {e}", level="error")
                    raise
            
            print_log("TODAS AS MATERIALIZED VIEWS FORAM CRIADAS", level="success")
            
        except Exception as e:
            print_log(f"ERRO AO CRIAR MATERIALIZED VIEWS: {e}", level="error")
            raise
        finally:
            if self.conn:
                self.conn.close()
                self.conn = None

    def refresh_materialized_views(self, concurrent: bool = True) -> None:
        """
        Atualiza todas as Materialized Views existentes no banco.
        
        Consulta pg_matviews para obter a lista de MVs e executa REFRESH
        para cada uma delas.
        
        Args:
            concurrent: Se True, usa REFRESH CONCURRENTLY (requer índice único).
                       Se False, usa REFRESH simples (bloqueia leituras).
        
        Pode ser executado via: python etl.py db views refresh [--concurrent]
        """
        try:
            print_log("ATUALIZANDO MATERIALIZED VIEWS...", level="task")
            
            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()
            
            # Consulta MVs existentes que começam com mv_
            cur.execute("""
                SELECT matviewname 
                FROM pg_matviews 
                WHERE schemaname = 'public' 
                  AND matviewname LIKE 'mv_%'
                ORDER BY matviewname;
            """)
            
            mv_names = [row[0] for row in cur.fetchall()]
            
            if not mv_names:
                print_log("NENHUMA MATERIALIZED VIEW ENCONTRADA", level="warning")
                return
            
            total = len(mv_names)
            width = len(str(total))
            
            # MVs que possuem índice único (podem usar CONCURRENTLY)
            mvs_with_unique_index = set()
            if concurrent:
                cur.execute("""
                    SELECT DISTINCT tablename 
                    FROM pg_indexes 
                    WHERE schemaname = 'public' 
                      AND tablename LIKE 'mv_%'
                      AND indexdef LIKE '%UNIQUE%';
                """)
                mvs_with_unique_index = {row[0] for row in cur.fetchall()}
            
            results = []
            
            for i, mv_name in enumerate(mv_names, start=1):
                try:
                    # Verifica se pode usar CONCURRENTLY
                    use_concurrent = concurrent and mv_name in mvs_with_unique_index
                    refresh_type = "CONCURRENTLY" if use_concurrent else ""
                    
                    print_log(f"[{i:0{width}}/{total}] REFRESH {refresh_type} {mv_name}...", level="docs")
                    
                    start_time = time.time()
                    
                    if use_concurrent:
                        cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv_name};")
                    else:
                        cur.execute(f"REFRESH MATERIALIZED VIEW {mv_name};")
                    
                    elapsed = time.time() - start_time
                    results.append((mv_name, "OK", elapsed))
                    
                    print_log(f"[{i:0{width}}/{total}] {mv_name} ATUALIZADA ({elapsed:.1f}s)", level="success")
                    
                except psycopg2.Error as e:
                    results.append((mv_name, "ERRO", str(e)))
                    print_log(f"[{i:0{width}}/{total}] ERRO EM {mv_name}: {e}", level="error")
            
            # Resumo
            ok_count = sum(1 for r in results if r[1] == "OK")
            err_count = sum(1 for r in results if r[1] == "ERRO")
            
            if err_count > 0:
                print_log(f"REFRESH CONCLUÍDO: {ok_count} OK, {err_count} ERRO(S)", level="warning")
            else:
                print_log(f"TODAS AS {ok_count} MATERIALIZED VIEWS FORAM ATUALIZADAS", level="success")
            
        except Exception as e:
            print_log(f"ERRO AO ATUALIZAR MATERIALIZED VIEWS: {e}", level="error")
            raise
        finally:
            if self.conn:
                self.conn.close()
                self.conn = None
