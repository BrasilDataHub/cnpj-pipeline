# db/postgres_builder.py

"""
PostgreSQL database construction module.
"""

import os
import time
import psycopg2
from pathlib import Path
from queue import Queue, Empty
from threading import Lock, Thread
from ..db.schema import SCHEMA
from ..db.advanced_indexes import (
    ADVANCED_INDEXES,
    REQUIRED_EXTENSIONS,
    INDEX_CREATION_CONFIG,
    build_create_index_sql,
)
from ..utils.db_patch import apply_static_fixes
from ..utils.logger import print_log
from ..config import PIPELINE_STATS_TABLE, INDEX_MAX_WORKERS, INDEX_MAINTENANCE_WORK_MEM

# Metadata tables that drop_tables() must not remove: they hold the run
# history and are independent from the RFB data.
PRESERVED_TABLES = {PIPELINE_STATS_TABLE}

# Session memory for MV builds/refreshes. The default work_mem (32 MB) turns
# the count(DISTINCT) aggregates over the 72M-row estabelecimento table into
# an external merge sort measured in hours; 1 GB keeps even the largest group
# (SP) fully in memory. Session-scoped on purpose: the server-wide setting
# stays untouched for the website's short queries.
MV_BUILD_WORK_MEM = os.getenv("MV_BUILD_WORK_MEM", "1GB")
MV_BUILD_MAINTENANCE_WORK_MEM = os.getenv("MV_BUILD_MAINTENANCE_WORK_MEM", "2GB")

# Which MV files must be rebuilt together, keyed by file-name prefix.
# The comparison MVs read the monthly series, so `DROP MATERIALIZED VIEW ...
# CASCADE` on a series silently takes its comparison MV with it: a
# `--only 05_` would leave the database WITHOUT mv_comparativo_territorio and
# report success. Selecting a series therefore always drags its dependents in.
MV_FILE_DEPENDENTS = {
    "05_": ["17_"],   # mv_abertura_periodo             -> mv_comparativo_territorio
    "14_": ["18_"],   # mv_movimentacao_mensal_cnae     -> mv_comparativo_cnae
    "15_": ["19_"],   # mv_movimentacao_mensal_natureza -> mv_comparativo_natureza
}


class PostgresBuilder:
    """
    Builds the PostgreSQL database.
    """

    def __init__(self, config):
        self.config = config
        self.conn = None

    def _connect(self):
        """Opens a Postgres connection."""
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
                # Quoted identifier: the name comes from configuration and must
                # not be interpolated into SQL unescaped.
                cur.execute(f'CREATE DATABASE "{db_name}"')
                print_log("BANCO CRIADO", level="success")
            conn.close()
        except psycopg2.Error as e:
            print_log(f"ERRO AO CRIAR BANCO: {e}", level="error")
            raise

    def enable_extensions(self):
        """
        Enables the extensions required for advanced indexes and operation.
        Extensions: pg_trgm (trigram text search), unaccent (accent
        normalization) and pg_stat_statements (query diagnostics; active
        collection requires preload on the instance).
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
        """Removes previous tables, preserving the metadata ones.

        `pipeline_stats` holds the run history and needs to survive a reload
        — without this exception, the drop would sweep all of `pg_tables` and
        erase exactly the record of what already ran.
        """
        try:
            conn = self._connect()
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
            for (table_name,) in cur.fetchall():
                if table_name in PRESERVED_TABLES:
                    continue
                cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
            conn.close()
            print_log("TABELAS ANTERIORES REMOVIDAS", level="docs")
        except psycopg2.Error as e:
            print_log(f"ERRO AO EXCLUIR TABELAS: {e}", level="error")
            raise

    def create_tables(self):
        """
        Creates all tables. Primary keys defined inline on columns (smaller
        tables) are created here. PKs of the big tables, defined separately in
        SCHEMA, are deferred.
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
        Adds primary keys ONLY for tables that define them separately in
        SCHEMA (e.g. 'empresa', 'estabelecimento').
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
        Applies static data fixes.
        Can be run independently via: python etl.py db patch
        """
        if self.conn is None:
            self.conn = self._connect()
        apply_static_fixes(self.conn)

    def add_primary_keys(self):
        """
        Adds primary keys on the big tables.
        Can be run independently via: python etl.py db pk
        """
        self._add_primary_keys()

    def patch_data(self):
        """
        [LEGACY] Applies static fixes + adds PKs.
        Kept for compatibility. Prefer apply_patches() and add_primary_keys() separately.
        """
        self.apply_patches()
        self.add_primary_keys()

    def set_tables_logged(self):
        """
        Converts tables from UNLOGGED to LOGGED at the end of the load.

        The ETL creates everything UNLOGGED to speed up COPY (no WAL), but an
        UNLOGGED table does not survive a crash: PostgreSQL TRUNCATES the
        contents on recovery — a simple instance crash would lose the whole
        database. LOGGED is also a precondition for physical backup/PITR and
        replicas.

        Cost: full rewrite with WAL, table by table (estimated +1–3h on the 5
        big tables; a high `max_wal_size` reduces checkpoints). The conversion
        happens right after load/patches and before PKs and indexes — with
        fewer relations existing, fewer bytes get rewritten.

        Idempotent: queries relpersistence and only converts what is still 'u'.

        Order: FK dependency, not size — PostgreSQL refuses to convert a table
        to LOGGED while it references (FK) a table that is still UNLOGGED. In
        the pipeline the FKs do not exist yet at this step and any order works;
        in standalone use (`db logged` against a database that already has
        FKs, like production) the wrong order aborts with "could not change
        table ... because it references unlogged table". Within each
        dependency level, converts smallest to largest (fails early if space/
        WAL runs out).

        Can be run independently via: python etl.py db logged
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

            # FK edges between still-UNLOGGED tables: the referencing table
            # can only be converted after the referenced one.
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
        """Creates the foreign keys defined in SCHEMA.

        Every FK is attempted; failures are collected and reported at the END
        as a step failure. Swallowing FK errors was exactly the silent failure
        mode of the 07/2026 incident (two FKs missing, nobody noticed).
        """
        try:
            print_log("CRIANDO CHAVES ESTRANGEIRAS...", level="task")
            if self.conn is None: self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()

            # 1. Collect all FK definitions to get a total.
            all_fks = []
            for table_name, definition in SCHEMA.items():
                if 'foreign_keys' in definition:
                    for i, fk_def in enumerate(definition['foreign_keys'], start=1):
                        all_fks.append((table_name, fk_def, i))

            # 2. Iterate over the FK list with a global counter.
            total = len(all_fks)
            width = len(str(total))
            errors = []
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
                    # 42710 = duplicate_object (constraint already exists)
                    if e.pgcode == '42710':
                        print_log(f"[{i:0{width}}/{total}] FK JÁ EXISTE: {constraint_name}, pulando.", level="docs")
                    else:
                        errors.append(f"{constraint_name}: {e}")
                        print_log(f"[{i:0{width}}/{total}] ERRO AO ADICIONAR FK '{constraint_name}': {e}",
                                  level="error")

            if errors:
                raise RuntimeError(
                    f"{len(errors)} CHAVE(S) ESTRANGEIRA(S) NÃO FORAM CRIADAS: "
                    + "; ".join(errors)
                )

            print_log("CHAVES ESTRANGEIRAS CRIADAS", level="success")
        except psycopg2.Error as e:
            print_log(f"ERRO AO CRIAR FKs: {e}", level="error")
            raise

    # -- index creation -------------------------------------------------------

    def _index_session_config(self) -> dict:
        """Session parameters applied to EVERY index-building connection.

        `maintenance_work_mem` comes from INDEX_MAINTENANCE_WORK_MEM (env) —
        note that peak memory is roughly workers × work_mem.
        """
        session_config = dict(INDEX_CREATION_CONFIG)
        session_config['maintenance_work_mem'] = INDEX_MAINTENANCE_WORK_MEM
        return session_config

    def _open_index_connection(self):
        """Dedicated connection for index builds, with tuned session params."""
        conn = self._connect()
        conn.autocommit = True
        cur = conn.cursor()
        for param, value in self._index_session_config().items():
            try:
                cur.execute(f"SET {param} = '{value}';")
            except psycopg2.Error as e:
                print_log(f"AVISO: não foi possível aplicar {param}={value}: {e}", level="warning")
        return conn, cur

    def _run_index_tasks(self, label: str, tasks: list,
                         parallel: bool = True, max_workers: int = None) -> list:
        """Executes a list of (index_name, sql) tasks and returns the errors.

        The target database has no readers during the pipeline (the website
        only switches to it after completion), so indexes are built WITHOUT
        `CONCURRENTLY` — one table scan instead of two. Plain CREATE INDEX
        takes a SHARE lock, which is self-compatible: several indexes of the
        SAME table can build in parallel, so the pool is fed per index, not
        per table (before, `estabelecimento` concentrated most indexes and ran
        sequentially).
        """
        total = len(tasks)
        if total == 0:
            return []

        max_workers = max_workers or INDEX_MAX_WORKERS
        effective_workers = max(1, min(max_workers, total)) if parallel else 1

        print_log(f"CRIANDO {total} {label} (workers={effective_workers})...", level="task")
        for param, value in self._index_session_config().items():
            print_log(f"  -> {param} = {value} (por conexão de worker)", level="docs")

        task_queue: Queue = Queue()
        for task in tasks:
            task_queue.put(task)

        progress_lock = Lock()
        progress = {'count': 0}
        errors: list = []
        width = len(str(total))

        def worker():
            conn = None
            try:
                conn, cur = self._open_index_connection()
                while True:
                    try:
                        index_name, sql = task_queue.get_nowait()
                    except Empty:
                        break
                    start_time = time.time()
                    try:
                        cur.execute(sql)
                        elapsed = time.time() - start_time
                        with progress_lock:
                            progress['count'] += 1
                            current = progress['count']
                        print_log(
                            f"[{current:0{width}}/{total}] ÍNDICE CRIADO: {index_name} ({elapsed:.1f}s)",
                            level="docs"
                        )
                    except psycopg2.Error as e:
                        with progress_lock:
                            progress['count'] += 1
                            errors.append(f"{index_name}: {e}")
                        print_log(f"ERRO AO CRIAR ÍNDICE {index_name}: {e}", level="error")
            except Exception as e:
                # Connection-level failure: everything left in the queue for
                # this worker is someone else's job now; if all workers die,
                # remaining tasks are reported below.
                with progress_lock:
                    errors.append(f"worker: {e}")
                print_log(f"ERRO EM WORKER DE ÍNDICES: {e}", level="error")
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        if effective_workers == 1:
            worker()
        else:
            threads = [Thread(target=worker) for _ in range(effective_workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Tasks never attempted (e.g. all workers failed to connect).
        unattempted = []
        while True:
            try:
                index_name, _ = task_queue.get_nowait()
                unattempted.append(index_name)
            except Empty:
                break
        if unattempted:
            errors.append(f"não tentados: {', '.join(unattempted)}")

        return errors

    @staticmethod
    def _basic_index_sql(table_name: str, index: dict) -> str:
        index_cols = ', '.join(f'"{col}"' for col in index['columns'])
        return f'CREATE INDEX IF NOT EXISTS "{index["name"]}" ON public."{table_name}" ({index_cols});'

    def create_indexes(self, parallel: bool = True, max_workers: int = None):
        """
        Creates all indexes: basic (defined in SCHEMA) and advanced
        (GIN, BRIN, HASH, partial, composite).

        Both phases attempt every index; any failure makes the step FAIL at
        the end (after everything was attempted), listing what is missing.

        Args:
            parallel: when True, builds indexes in parallel connections.
            max_workers: worker pool size (default: INDEX_MAX_WORKERS).
        """
        try:
            # Phase 1: basic indexes (SCHEMA).
            basic_tasks = []
            for table_name, definition in SCHEMA.items():
                for index in definition.get('indexes', []):
                    basic_tasks.append((index['name'], self._basic_index_sql(table_name, index)))

            errors = self._run_index_tasks("ÍNDICES", basic_tasks, parallel, max_workers)

            # Phase 2: advanced indexes (GIN, BRIN, HASH, partial, composite).
            advanced_tasks = [
                (index_def.get('name', 'desconhecido'), build_create_index_sql(index_def, concurrent=False))
                for index_def in ADVANCED_INDEXES
            ]
            errors += self._run_index_tasks("ÍNDICES AVANÇADOS", advanced_tasks, parallel, max_workers)

            if errors:
                raise RuntimeError(
                    f"{len(errors)} ÍNDICE(S) NÃO FORAM CRIADOS: " + "; ".join(errors)
                )

            print_log("TODOS OS ÍNDICES FORAM CRIADOS COM SUCESSO", level="success")

        except Exception as e:
            print_log(f"ERRO AO CRIAR ÍNDICES: {e}", level="error")
            raise

    def create_advanced_indexes(self, parallel: bool = True, max_workers: int = None):
        """
        Creates only the advanced indexes. Kept as a public entry point;
        `create_indexes` covers the full flow.
        """
        advanced_tasks = [
            (index_def.get('name', 'desconhecido'), build_create_index_sql(index_def, concurrent=False))
            for index_def in ADVANCED_INDEXES
        ]
        if not advanced_tasks:
            print_log("NENHUM ÍNDICE AVANÇADO DEFINIDO", level="warning")
            return

        errors = self._run_index_tasks("ÍNDICES AVANÇADOS", advanced_tasks, parallel, max_workers)
        if errors:
            raise RuntimeError(
                f"{len(errors)} ÍNDICE(S) AVANÇADO(S) NÃO FORAM CRIADOS: " + "; ".join(errors)
            )
        print_log("TODOS OS ÍNDICES AVANÇADOS FORAM CRIADOS COM SUCESSO", level="success")

    def initialize_schema(self) -> None:
        """Runs the full schema creation flow."""
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
        """Returns the path of the sql/materialized_views/ folder."""
        # Walks up from db/ -> rfb_cnpj_etl/ -> src/ -> project/ -> sql/materialized_views/
        base_dir = Path(__file__).resolve().parents[3]
        return base_dir / "sql" / "materialized_views"

    @staticmethod
    def _expand_mv_selection(selection: list) -> list:
        """Adds to `selection` the prefixes that depend on what was selected.

        Runs to a fixed point so a chain (A -> B -> C) is fully covered even
        though today's map is only one level deep.
        """
        expanded = list(dict.fromkeys(selection))
        added = True
        while added:
            added = False
            for source, dependents in MV_FILE_DEPENDENTS.items():
                if not any(source in item or item in source for item in expanded):
                    continue
                for dependent in dependents:
                    if dependent not in expanded:
                        expanded.append(dependent)
                        added = True
                        print_log(
                            f"DEPENDÊNCIA: '{dependent}' INCLUÍDO PORQUE '{source}' "
                            f"FOI SELECIONADO (DROP CASCADE O DERRUBARIA)",
                            level="warning",
                        )
        return expanded

    def create_materialized_views(self, only: list = None) -> None:
        """
        Creates all Materialized Views from the SQL files.

        The SQL files are read from sql/materialized_views/ and executed
        sequentially in alphabetical order (numbering in the file name).

        Args:
            only: when given, only files whose name contains one of these
                  substrings are executed (e.g. ["05", "14"]). The selection is
                  expanded with the dependent files (see MV_FILE_DEPENDENTS).

        Can be run via: python etl.py db views create [--only 05,14]
        """
        try:
            print_log("CRIANDO MATERIALIZED VIEWS...", level="task")

            sql_dir = self._get_mv_sql_dir()
            if not sql_dir.exists():
                raise FileNotFoundError(f"Pasta não encontrada: {sql_dir}")

            # List .sql files ordered by name.
            sql_files = sorted(sql_dir.glob("*.sql"))

            if only:
                patterns = self._expand_mv_selection(only)
                selected = [f for f in sql_files
                            if any(p in f.name for p in patterns)]
                unmatched = [p for p in patterns
                             if not any(p in f.name for f in sql_files)]
                if unmatched:
                    raise ValueError(
                        "NENHUM ARQUIVO SQL CORRESPONDE A: " + ", ".join(unmatched)
                    )
                sql_files = selected
                print_log(
                    "SELEÇÃO: " + ", ".join(f.name for f in sql_files),
                    level="docs",
                )

            if not sql_files:
                print_log("NENHUM ARQUIVO SQL ENCONTRADO", level="warning")
                return

            total = len(sql_files)
            width = len(str(total))

            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()
            self._apply_mv_session_settings(cur)

            for i, sql_file in enumerate(sql_files, start=1):
                file_name = sql_file.name
                try:
                    print_log(f"[{i:0{width}}/{total}] EXECUTANDO: {file_name}...", level="docs")

                    sql_content = sql_file.read_text(encoding="utf-8")

                    # psycopg2 accepts multiple statements separated by ;
                    start_time = time.time()
                    cur.execute(sql_content)
                    elapsed = time.time() - start_time

                    print_log(f"[{i:0{width}}/{total}] CONCLUÍDO: {file_name} ({elapsed:.1f}s)", level="success")

                except psycopg2.Error as e:
                    print_log(f"[{i:0{width}}/{total}] ERRO EM {file_name}: {e}", level="error")
                    raise

            if only:
                print_log(f"{total} MATERIALIZED VIEW(S) SELECIONADA(S) FORAM CRIADAS", level="success")
            else:
                print_log("TODAS AS MATERIALIZED VIEWS FORAM CRIADAS", level="success")

        except Exception as e:
            print_log(f"ERRO AO CRIAR MATERIALIZED VIEWS: {e}", level="error")
            raise
        finally:
            if self.conn:
                self.conn.close()
                self.conn = None

    @staticmethod
    def _apply_mv_session_settings(cur) -> None:
        """Raises the session memory limits for MV builds/refreshes.

        SET is session-scoped: it dies with the connection and never touches
        the server configuration used by the website's queries.
        """
        cur.execute("SET work_mem = %s", (MV_BUILD_WORK_MEM,))
        cur.execute("SET maintenance_work_mem = %s", (MV_BUILD_MAINTENANCE_WORK_MEM,))
        # Parallel hash joins allocate their hash table in dynamic shared
        # memory (/dev/shm), sized by work_mem — with 1 GB that bursts the
        # default 64 MB shm of a dockerized Postgres ("could not resize shared
        # memory segment ... No space left on device", seen in production on
        # mv_stats_natureza_juridica_cnae). Leader-only execution keeps every
        # allocation in local memory and works on any host without shm tuning.
        cur.execute("SET max_parallel_workers_per_gather = 0")
        print_log(
            f"SESSÃO DE BUILD: work_mem={MV_BUILD_WORK_MEM}, "
            f"maintenance_work_mem={MV_BUILD_MAINTENANCE_WORK_MEM}, "
            "max_parallel_workers_per_gather=0",
            level="docs",
        )

    @staticmethod
    def _mv_refresh_order(cur) -> list:
        """Returns the MV names in dependency order (dependencies first).

        Alphabetical order is wrong here: `mv_comparativo_*` sorts BEFORE
        `mv_movimentacao_*` but reads from it, so an alphabetical refresh would
        publish a comparison built on the previous month's series. The real
        graph lives in the catalog — pg_rewrite holds the MV's query and
        pg_depend the relations that query touches.

        Falls back to alphabetical order on any failure (including a dependency
        cycle, which cannot happen with MVs but would otherwise hang the loop):
        a suboptimal order still refreshes everything.
        """
        cur.execute("""
            SELECT matviewname
            FROM pg_matviews
            WHERE schemaname = 'public'
              AND matviewname LIKE 'mv_%'
            ORDER BY matviewname;
        """)
        alphabetical = [row[0] for row in cur.fetchall()]

        try:
            cur.execute("""
                SELECT DISTINCT dependente.relname, referenciada.relname
                FROM pg_depend d
                JOIN pg_rewrite r ON r.oid = d.objid
                JOIN pg_class dependente ON dependente.oid = r.ev_class
                JOIN pg_class referenciada ON referenciada.oid = d.refobjid
                JOIN pg_namespace n ON n.oid = dependente.relnamespace
                WHERE d.classid = 'pg_rewrite'::regclass
                  AND d.refclassid = 'pg_class'::regclass
                  AND dependente.relkind = 'm'
                  AND referenciada.relkind = 'm'
                  AND dependente.oid <> referenciada.oid
                  AND n.nspname = 'public';
            """)
            known = set(alphabetical)
            # requires[mv] = MVs that must be refreshed before it.
            requires = {name: set() for name in alphabetical}
            for dependent, source in cur.fetchall():
                if dependent in known and source in known:
                    requires[dependent].add(source)

            ordered = []
            pending = list(alphabetical)
            while pending:
                ready = [name for name in pending
                         if not (requires[name] - set(ordered))]
                if not ready:
                    raise RuntimeError("ciclo de dependências entre MVs")
                ordered.extend(ready)
                pending = [name for name in pending if name not in ready]

            if ordered != alphabetical:
                print_log("ORDEM DE REFRESH AJUSTADA POR DEPENDÊNCIA ENTRE MVs", level="docs")
            return ordered

        except Exception as e:
            print_log(
                f"ORDENAÇÃO TOPOLÓGICA INDISPONÍVEL ({e}); USANDO ORDEM ALFABÉTICA",
                level="warning",
            )
            return alphabetical

    def refresh_materialized_views(self, concurrent: bool = True) -> None:
        """
        Refreshes every Materialized View in the database.

        Queries pg_matviews for the MV list and runs REFRESH on each one.

        Args:
            concurrent: when True, uses REFRESH CONCURRENTLY (requires a
                        unique index). When False, plain REFRESH (blocks reads).

        Can be run via: python etl.py db views refresh [--concurrent]
        """
        try:
            print_log("ATUALIZANDO MATERIALIZED VIEWS...", level="task")

            if self.conn is None:
                self.conn = self._connect()
            self.conn.autocommit = True
            cur = self.conn.cursor()
            self._apply_mv_session_settings(cur)

            # Existing MVs whose name starts with mv_, dependencies first.
            mv_names = self._mv_refresh_order(cur)

            if not mv_names:
                print_log("NENHUMA MATERIALIZED VIEW ENCONTRADA", level="warning")
                return

            total = len(mv_names)
            width = len(str(total))

            # MVs that have a unique index (may use CONCURRENTLY).
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

            # Summary.
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
