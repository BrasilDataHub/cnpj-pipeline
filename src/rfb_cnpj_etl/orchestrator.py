# orchestrator.py

"""
Data load orchestration.
"""

import os
from typing import Optional
from .cnpj_data import CNPJDataScraper
from .db import PostgresBuilder, run_postgres_loader, load_ibge_tables, build_search_table
from .utils.logger import print_log
from .utils.zip_metadata import validate_zip_files, estimate_total_lines_from_size
from .utils.run_state import (
    run_step,
    STEP_VALIDATION,
    STEP_SCHEMA,
    STEP_LOAD,
    STEP_PATCHES,
    STEP_LOGGED,
    STEP_PK,
    STEP_INDEXES,
    STEP_FK,
    STEP_SEARCH,
    STEP_VIEWS,
)
from .config import (
    DEFAULT_PARALLEL,
    DEFAULT_LOW_MEMORY,
    DOWNLOAD_DIR,
    POSTGRES
)


def run_orchestrator(
        command: Optional[str] = "load",
        db_name: Optional[str] = POSTGRES["database"],
        month_year: Optional[str] = None,
        files_dir: Optional[str] = None,
        skip_indexes: bool = False,
        skip_validation: bool = False,
        parallel: bool = DEFAULT_PARALLEL,
        low_memory: bool = DEFAULT_LOW_MEMORY,
        only_data: bool = False,
        concurrent: bool = False,
        views_only: Optional[list] = None,
        state=None
):
    """
    Orchestrates the PostgreSQL database work.

    Available commands:
        - init: Creates schema and tables
        - load: Loads data (with or without --only-data)
        - patch: Applies static data fixes
        - logged: Converts UNLOGGED tables to LOGGED (durability)
        - pk: Adds primary keys
        - index: Creates all indexes (basic + advanced)
        - fk: Creates foreign keys
        - search: Builds/rebuilds the search table (build-and-swap)
        - views-create: Creates/recreates the Materialized Views
        - views-refresh: Refreshes the Materialized Views

    :params:
        command: command to execute.
        db_name: Postgres database name.
        month_year: month/year to load ("MM/YYYY").
        files_dir: directory containing the source ZIP files.
        skip_indexes: skip index creation.
        skip_validation: skip source file validation.
        parallel: use threads for processing.
        low_memory: use constrained-memory mode.
        only_data: when True, loads data only, without patch/pk/index/fk.
        concurrent: when True, uses REFRESH CONCURRENTLY for views (requires unique index).
        views_only: for views-create, list of file-name substrings to rebuild
                    (e.g. ["05", "14"]); None rebuilds every MV.
        state: optional RunState for checkpoint/resume. When None (default),
               steps run exactly as before, without tracking.

    :return: run metrics dict (e.g. {"records_inserted": 218380000}).
    """
    print_log("INICIANDO TAREFAS DO BANCO DE DADOS...", level="start")

    estimated_lines = None

    # For load commands, resolve directories and validate files first.
    if command == "load":
        data = CNPJDataScraper()

        if month_year is None:
            month_year = data.get_latest()

        if files_dir is None:
            mm, yyyy = month_year.split('/')
            folder = f"{yyyy}-{mm}"
            files_dir = os.path.join(DOWNLOAD_DIR, folder)

        def _validate_files():
            try:
                if not skip_validation and not validate_zip_files(month_year, files_dir):
                    print_log("EXECUÇÃO INTERROMPIDA. VERIFIQUE OS ARQUIVOS NO DIRETÓRIO LOCAL.", level="error")
                    raise ValueError("Validação dos arquivos ZIP falhou.")
            except FileNotFoundError:
                hint = (
                    f"PASTA NÃO ENCONTRADA PARA {month_year}: {files_dir}. "
                    f"Baixe os arquivos antes de carregar: "
                    f"`python etl.py download --month {month_year}` "
                    f"ou use `python etl.py complete --month {month_year}`. "
                    f"Para outro caminho, passe --download-dir."
                )
                print_log(hint, level="error")
                raise ValueError(hint)

        run_step(state, STEP_VALIDATION, _validate_files)

        # Estimate total rows to drive the progress bar/percentage.
        estimated_lines = estimate_total_lines_from_size(files_dir)

    # PostgreSQL connection configuration.
    postgres_config = POSTGRES.copy()
    if db_name and db_name != POSTGRES["database"]:
        postgres_config["database"] = db_name
    builder = PostgresBuilder(config=postgres_config)

    # Metrics returned to the caller to feed `pipeline_stats`.
    metrics = {"records_inserted": None}

    def _init_schema():
        builder.initialize_schema()
        load_ibge_tables(postgres_config=postgres_config)

    def _load_data():
        # Publishes to the state which table/file is being loaded and how far
        # along it is — this is what the dashboard shows inside `data_load`.
        def _on_load_progress(table, filename, inserted, total):
            if state is None:
                return
            pct = round(inserted / total * 100, 1) if total else None
            state.progress(
                STEP_LOAD,
                current_table=table,
                current_file=os.path.basename(filename or ""),
                records_inserted=int(inserted),
                records_total=int(total or 0),
                percent=pct,
            )

        result = run_postgres_loader(
            files_dir=files_dir,
            postgres_config=postgres_config,
            total_records=estimated_lines,
            parallel=parallel,
            low_memory=low_memory,
            on_progress=_on_load_progress,
        )

        # A batch lost after retries is usually bad SOURCE data (the RFB ships
        # malformed rows now and then) — it must never sink an hours-long run.
        # The batch is already isolated in the dead-letter directory; here we
        # make the loss loud (log) and durable (step metadata → dashboard),
        # and the pipeline moves on. Reprocessing (after a manual fix, if
        # needed) is done with: python etl.py db dead-letter --retry
        if result["failed_batches"]:
            dead_letter_files = ", ".join(
                os.path.basename(p) for p in result["dead_letter_files"]
            ) or "nenhum arquivo gravado"
            print_log(
                f"CARGA CONCLUÍDA COM PERDAS: {result['failed_batches']} lote(s) "
                f"({result['failed_rows']} linhas) falharam após retentativas e foram "
                f"preservados em dead-letter ({dead_letter_files}). O pipeline SEGUE "
                f"normalmente. Analise/corrija e reprocesse com: "
                f"python etl.py db dead-letter --retry",
                level="warning"
            )

        return result

    def _load_step_metadata(result):
        """Step metadata for the state/dashboard, including any losses."""
        metadata = {"records_inserted": int((result or {}).get("records_inserted") or 0)}
        if result and result.get("failed_batches"):
            metadata["failed_batches"] = int(result["failed_batches"])
            metadata["failed_rows"] = int(result["failed_rows"])
            metadata["dead_letter_files"] = [
                os.path.basename(p) for p in result["dead_letter_files"]
            ]
        return metadata

    # =========================================================================
    # STEP 1: Schema initialization (init or load)
    # =========================================================================
    if command in ("init", "load"):
        run_step(state, STEP_SCHEMA, _init_schema)

    # =========================================================================
    # STEP 2: Data load (load)
    # =========================================================================
    if command == "load":
        load_result = run_step(
            state, STEP_LOAD, _load_data,
            metadata_fn=_load_step_metadata
        )
        if load_result is not None:
            metrics["records_inserted"] = int(load_result.get("records_inserted") or 0)

    # =========================================================================
    # STEP 3: Static data fixes (patch, or load without --only-data)
    # =========================================================================
    if command == "patch" or (command == "load" and not only_data):
        run_step(state, STEP_PATCHES, builder.apply_patches)

    # =========================================================================
    # STEP 3.5: Convert tables to LOGGED (logged, or load without --only-data)
    # Before PKs/indexes: with fewer relations, the WAL rewrite is smaller.
    # UNLOGGED does not survive a crash — without this step, a crash truncates
    # the whole database.
    # =========================================================================
    if command == "logged" or (command == "load" and not only_data):
        run_step(state, STEP_LOGGED, builder.set_tables_logged)

    # =========================================================================
    # STEP 4: Primary keys (pk, or load without --only-data)
    # =========================================================================
    if command == "pk" or (command == "load" and not only_data):
        run_step(state, STEP_PK, builder.add_primary_keys)

    # =========================================================================
    # STEP 5: Indexes (index, or load without --only-data and --skip-index)
    # =========================================================================
    if command == "index" or (command == "load" and not only_data and not skip_indexes):
        run_step(state, STEP_INDEXES, builder.create_indexes)

    # =========================================================================
    # STEP 6: Foreign keys (fk, or load without --only-data)
    # =========================================================================
    if command == "fk" or (command == "load" and not only_data):
        run_step(state, STEP_FK, builder.enable_foreign_keys)

    # =========================================================================
    # STEP 6.5: Lean search table (search, or load without --only-data)
    # Post-load: depends on the final estabelecimento/empresa data and on the
    # unaccent extension. Build-and-swap — reads never become unavailable.
    # =========================================================================
    if command == "search" or (command == "load" and not only_data):
        run_step(state, STEP_SEARCH, lambda: build_search_table(postgres_config))

    # =========================================================================
    # STEP 7: Materialized Views (optional commands)
    # =========================================================================
    if command == "views-create":
        run_step(state, STEP_VIEWS,
                 lambda: builder.create_materialized_views(only=views_only))

    if command == "views-refresh":
        # Refresh is not a build step: it does not enter the state, because
        # re-running it is always valid and there is nothing to "resume".
        builder.refresh_materialized_views(concurrent=concurrent)

    print_log(f"EXECUÇÃO FINALIZADA | POSTGRES | {month_year or command}", level="done")
    return metrics
