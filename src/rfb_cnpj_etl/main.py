# main.py

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from .orchestrator import run_orchestrator
from .cnpj_data import CNPJDataScraper, CNPJDownloadManager
from .utils.logger import print_log, set_log_file
from .utils.run_state import (
    RunState, run_step, normalize_reference_period, now_iso, STEP_DOWNLOAD
)
from .utils.webhook import WebhookNotifier
from .utils.dashboard import start_dashboard
from .utils.environment import collect_environment, collect_database_info
from .config import (
    DEFAULT_PARALLEL, DEFAULT_LOW_MEMORY, POSTGRES, BASE_DIR, DATA_DIR,
    DASHBOARD_DEFAULT_PORT, MAX_STEP_ATTEMPTS, DASHBOARD_USER, DASHBOARD_PASSWORD
)


def str2bool(value):
    if isinstance(value, bool):
        return value
    if value.lower() in ("yes", "true", "t", "1"):
        return True
    elif value.lower() in ("no", "false", "f", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Valor deve ser true ou false")


def _resolve_log_file_path(cli_value: str = None) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    raw_value = cli_value or os.getenv("LOG_FILE")

    if not raw_value:
        return str(DATA_DIR / "logs" / f"etl-{date_str}.log")

    expanded = os.path.expanduser(raw_value)
    raw_path = Path(expanded)
    if not raw_path.is_absolute():
        raw_path = BASE_DIR / raw_path

    raw_path_str = str(raw_path)
    if "{date}" in raw_path_str:
        return raw_path_str.replace("{date}", date_str)

    if expanded.endswith(os.sep) or (raw_path.exists() and raw_path.is_dir()):
        return str(raw_path / f"etl-{date_str}.log")

    if raw_path.suffix:
        return str(raw_path.with_name(f"{raw_path.stem}-{date_str}{raw_path.suffix}"))

    return str(raw_path.with_name(f"{raw_path.name}-{date_str}.log"))


def _observability_parser() -> argparse.ArgumentParser:
    """State, dashboard and webhook flags, shared by the subcommands.

    They live in a parent parser (`parents=`) so they appear after the
    subcommand (`etl.py complete --serve`), which is where users expect them.
    """
    p = argparse.ArgumentParser(add_help=False)
    group = p.add_argument_group("observabilidade")
    group.add_argument(
        "--force", "--force-restart", dest="force", action="store_true",
        help="Ignora o estado existente do período e reexecuta tudo do zero "
             "(o estado anterior é preservado como .bak-<timestamp>)"
    )
    group.add_argument(
        "--no-state", action="store_true",
        help="Desliga o checkpoint/retomada (não lê nem grava arquivo de estado)"
    )
    group.add_argument(
        "--reference-period", type=str,
        help="Período dos dados a que esta execução pertence (AAAA-MM ou MM/AAAA). "
             "Só é necessário em subcomandos sem --month (ex.: db init, db fk); "
             "sem ele, usa-se o estado mais recente"
    )
    group.add_argument(
        "--max-attempts", type=int, default=MAX_STEP_ATTEMPTS,
        help=f"Tentativas por etapa antes de exigir intervenção "
             f"(padrão: {MAX_STEP_ATTEMPTS}; 0 = ilimitado)"
    )
    group.add_argument(
        "--serve", action="store_true",
        help="Sobe o dashboard web somente leitura durante a execução"
    )
    group.add_argument(
        "--port", type=int, default=DASHBOARD_DEFAULT_PORT,
        help=f"Porta do dashboard (padrão: {DASHBOARD_DEFAULT_PORT})"
    )
    group.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Interface do dashboard (padrão: 127.0.0.1; use 0.0.0.0 em container)"
    )
    group.add_argument(
        "--dashboard-password", type=str, default=DASHBOARD_PASSWORD,
        help="Senha do dashboard (Basic Auth). Se omitida, uma é gerada e "
             "mostrada no log. Equivale a PIPELINE_DASHBOARD_PASSWORD"
    )
    group.add_argument(
        "--dashboard-user", type=str, default=DASHBOARD_USER,
        help=f"Usuário do dashboard (padrão: {DASHBOARD_USER})"
    )
    group.add_argument(
        "--no-auth", action="store_true",
        help="Serve o dashboard sem autenticação (só em rede confiável)"
    )
    group.add_argument(
        "--webhook-url", type=str,
        help="URL para notificações por etapa (tem prioridade sobre PIPELINE_WEBHOOK_URL)"
    )
    return p


def _resolve_month(args) -> Optional[str]:
    """Resolves the reference month ("MM/YYYY") exactly once.

    Resolving here — instead of inside each component — guarantees that
    state, webhooks and download all talk about the same period, and avoids
    querying the RFB twice.
    """
    # --reference-period is the explicit declaration and takes priority: it
    # exists precisely for the subcommands that have no --month.
    explicit_period = getattr(args, "reference_period", None)
    if explicit_period:
        return explicit_period
    month = getattr(args, "month", None)
    if month:
        return month
    if args.command in ("complete", "download"):
        try:
            return CNPJDataScraper().get_latest()
        except Exception as exc:
            print_log(f"NÃO FOI POSSÍVEL RESOLVER O MÊS MAIS RECENTE: {exc}", level="warning")
    return None


def _db_config(db_name: Optional[str]) -> dict:
    """Connection configuration for this run's target database."""
    config = POSTGRES.copy()
    if db_name:
        config["database"] = db_name
    return config


def _init_observability(args, month: Optional[str],
                        db_name: Optional[str] = None) -> Tuple[Optional[RunState], object]:
    """Prepares state, webhooks and dashboard. Returns (state, dashboard_server)."""
    notifier = WebhookNotifier.from_config(getattr(args, "webhook_url", None))

    if getattr(args, "no_state", False):
        if getattr(args, "serve", False):
            print_log("--serve IGNORADO: --no-state DESLIGA O ARQUIVO DE ESTADO", level="warning")
        return None, None

    period = normalize_reference_period(month) or RunState.latest_period()
    if not period:
        print_log(
            "SEM PERÍODO DE REFERÊNCIA CONHECIDO — RASTREAMENTO DE ESTADO DESABILITADO "
            "(informe --month para habilitar)",
            level="warning"
        )
        return None, None

    # The collector runs at the end of each step: it shows the database
    # growing during the load instead of a frozen snapshot from the start.
    db_config = _db_config(db_name)
    state = RunState.load_or_create(
        reference_period=period,
        force=getattr(args, "force", False),
        max_attempts=getattr(args, "max_attempts", MAX_STEP_ATTEMPTS),
        notifier=notifier,
        db_info_fn=(lambda: collect_database_info(db_config)) if db_name else None,
    )
    print_log(f"ESTADO: {state.path}", level="folder")

    environment = collect_environment()
    database_info = collect_database_info(db_config) if db_name else {}
    state.set_environment(environment, database_info)
    if environment:
        where = environment.get("hostname") or "?"
        ip = environment.get("ip")
        print_log(
            f"AMBIENTE: {environment.get('runtime')} em {where}"
            + (f" ({ip})" if ip else "")
            + f" · Python {environment.get('python')} · {environment.get('os')}",
            level="docs"
        )

    server = None
    if getattr(args, "serve", False):
        server = start_dashboard(
            state_path=state.path,
            port=getattr(args, "port", DASHBOARD_DEFAULT_PORT),
            host=getattr(args, "host", "127.0.0.1"),
            password=getattr(args, "dashboard_password", None),
            user=getattr(args, "dashboard_user", DASHBOARD_USER),
            auth=not getattr(args, "no_auth", False),
        )

    state.pipeline_started()
    return state, server


def _download_details(dm: "CNPJDownloadManager") -> list:
    """Metadata of the downloaded files, for `files_downloaded_detail`.

    Reads size and mtime from disk: it is the only source that reflects what
    actually got written, including downloads resumed from a previous run.
    """
    details = []
    for path, url in zip(dm.file_paths, dm.file_urls):
        try:
            st = os.stat(path)
        except OSError:
            continue
        details.append({
            "filename": os.path.basename(path),
            "size_bytes": st.st_size,
            "source_url": url,
            "downloaded_at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
        })
    return details


def _download_progress(state):
    """Callback that publishes download progress to the state."""
    def _cb(downloaded, total, filename):
        if state is None:
            return
        state.progress(
            STEP_DOWNLOAD,
            files_downloaded=int(downloaded),
            files_total=int(total),
            files_remaining=int(total) - int(downloaded),
            current_file=os.path.basename(filename or ""),
            percent=round(downloaded / total * 100, 1) if total else None,
        )
    return _cb


def _download_summary(details: list) -> dict:
    """Download step metadata, displayed on the dashboard."""
    return {
        "files_downloaded": len(details),
        "total_bytes": sum(d.get("size_bytes", 0) for d in details),
    }


def _shutdown_dashboard(server, args) -> None:
    """Shuts the dashboard down at the end of the run.

    It dies with the process on purpose: keeping the server up would hang
    cron runs. The final state remains in the JSON file, which can be served
    later by any static server.
    """
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass
    print_log("DASHBOARD ENCERRADO (estado final permanece no arquivo JSON)", level="docs")


def _record_stats_start(state: Optional[RunState], db_name: str) -> None:
    """Writes `started_at` into pipeline_stats. Never interrupts the pipeline."""
    if state is None:
        return
    from .db import pipeline_stats
    conn = _stats_connection(db_name)
    if conn is None:
        return
    try:
        pipeline_stats.start_run(
            conn, run_id=state.run_id,
            reference_period=state.reference_period,
            started_at=state.data.get("created_at") or now_iso(),
        )
    finally:
        conn.close()


def _record_stats_finish(
        state: Optional[RunState], db_name: str, status: str,
        records: Optional[int] = None, downloads: Optional[list] = None,
        error: Optional[str] = None,
) -> None:
    """Closes the pipeline_stats row with the totals. Never interrupts."""
    if state is None:
        return
    from .db import pipeline_stats
    conn = _stats_connection(db_name)
    if conn is None:
        return
    try:
        pipeline_stats.finish_run(
            conn,
            run_id=state.run_id,
            finished_at=now_iso(),
            status=status,
            records_inserted_total=records,
            tables_populated=pipeline_stats.collect_tables_populated(conn),
            files_downloaded=downloads,
            error=error,
        )
    finally:
        conn.close()


def _stats_connection(db_name: str):
    """Dedicated statistics connection. Returns None when the database is down."""
    import psycopg2
    config = POSTGRES.copy()
    if db_name:
        config["database"] = db_name
    try:
        return psycopg2.connect(**config)
    except Exception as exc:
        print_log(f"ESTATÍSTICAS INDISPONÍVEIS (sem conexão): {exc}", level="warning")
        return None


def _complete_files_dir(download_dir: Optional[str], month: Optional[str]) -> Optional[str]:
    """Resolves the load directory for the `complete` command.

    The download manager always writes into `<download_dir>/<YYYY-MM>/`, so
    the load must read from the same subfolder. Without this, a custom
    `--download-dir X` downloaded into `X/2026-07` but tried to load from
    `X`, failing with "PASTA NÃO ENCONTRADA".
    """
    if not download_dir:
        return None
    month_ref = month or CNPJDataScraper().get_latest()
    period = normalize_reference_period(month_ref)   # "YYYY-MM"
    return os.path.join(download_dir, period)


def main() -> None:
    obs = _observability_parser()
    parser = argparse.ArgumentParser(
        description="Aplicação para consultar, baixar e carregar dados do CNPJ"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Caminho do arquivo de log (append). Aceita {date} para rotação diária"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # RFB
    sub.add_parser("get-availables", help="Lista meses disponíveis")
    sub.add_parser("get-latest", help="Mês mais recente disponível")
    p_urls = sub.add_parser("get-urls", help="Exibe URLs de um mês")
    p_urls.add_argument("--month", type=str, help="MM/AAAA")

    # DOWNLOAD
    p_dl = sub.add_parser("download", parents=[obs], help="Baixa ZIPs de um ou mais meses")
    p_dl.add_argument("--month", type=str, help="Mês no formato MM/AAAA (ex: 03/2025)")
    p_dl.add_argument("--clean", action="store_true", help="Remove arquivos antigos")
    p_dl.add_argument("--workers", type=int, help="Número de downloads simultâneos")
    p_dl.add_argument("--download-dir", type=str, help="Diretório para salvar os arquivos")

    # DB
    db_cmd = sub.add_parser("db", help="Comandos relacionados ao banco de dados")
    db_sub = db_cmd.add_subparsers(dest="db_command", required=True)

    # db-init
    p_init = db_sub.add_parser("init", parents=[obs], help="Inicializa o banco de dados")
    p_init.add_argument("--db-name", type=str, help="Nome do banco Postgres", default=POSTGRES["database"])

    # db-load
    p_load = db_sub.add_parser("load", parents=[obs], help="Carrega dados CSV para o banco")
    p_load.add_argument("--db-name", type=str, help="Nome do banco Postgres", default=POSTGRES["database"])
    p_load.add_argument("--month", type=str)
    p_load.add_argument("--download-dir", type=str)
    p_load.add_argument("--skip-index", action="store_true")
    p_load.add_argument("--skip-validation", action="store_true")
    p_load.add_argument("--low-memory", action="store_true")
    p_load.add_argument("--parallel", type=str2bool, nargs="?", const=True,
                        default=DEFAULT_PARALLEL, help="Multithread para Postgres (True/False)")
    p_load.add_argument("--only-data", action="store_true",
                        help="Carrega apenas os dados, sem executar patch/pk/index/fk")

    # db-index
    p_index = db_sub.add_parser("index", parents=[obs], help="Cria índices no banco")
    p_index.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-patch
    p_patch = db_sub.add_parser("patch", parents=[obs], help="Aplica correções estáticas na base de dados")
    p_patch.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-logged
    p_logged = db_sub.add_parser("logged", parents=[obs], help="Converte tabelas UNLOGGED para LOGGED (durabilidade)")
    p_logged.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-pk
    p_pk = db_sub.add_parser("pk", parents=[obs], help="Adiciona chaves primárias nas tabelas grandes")
    p_pk.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-fk
    p_fk = db_sub.add_parser("fk", parents=[obs], help="Cria chaves estrangeiras no banco")
    p_fk.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-search
    p_search = db_sub.add_parser(
        "search",
        parents=[obs],
        help="Constrói/reconstrói a tabela de busca busca_estabelecimento (build-and-swap)"
    )
    p_search.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-dead-letter
    p_dead_letter = db_sub.add_parser(
        "dead-letter",
        help="Lista e reprocessa lotes de COPY preservados após falha na carga"
    )
    p_dead_letter.add_argument("--db-name", type=str, default=POSTGRES["database"],
                               help="Nome do banco Postgres")
    p_dead_letter.add_argument("--retry", action="store_true",
                               help="Tenta recarregar cada lote; os que carregarem vão para processed/")
    p_dead_letter.add_argument("--dir", type=str,
                               help="Diretório dead-letter (padrão: PIPELINE_DEAD_LETTER_DIR)")

    # db-views (subcommand with create and refresh)
    views_cmd = db_sub.add_parser("views", help="Comandos para Materialized Views")
    views_sub = views_cmd.add_subparsers(dest="views_command", required=True)

    # db views create
    p_views_create = views_sub.add_parser("create", parents=[obs], help="Cria/recria as Materialized Views")
    p_views_create.add_argument("--db-name", type=str, default=POSTGRES["database"],
                                help="Nome do banco Postgres")

    # db views refresh
    p_views_refresh = views_sub.add_parser("refresh", help="Atualiza as Materialized Views")
    p_views_refresh.add_argument("--db-name", type=str, default=POSTGRES["database"],
                                 help="Nome do banco Postgres")
    p_views_refresh.add_argument("--concurrent", action="store_true",
                                 help="Usar REFRESH CONCURRENTLY (requer índice único)")

    # complete
    p_complete = sub.add_parser("complete", parents=[obs], help="Baixa e carrega dados automaticamente")
    p_complete.add_argument("--month", type=str)
    p_complete.add_argument("--download-dir", type=str)
    p_complete.add_argument("--db-name", type=str, default=POSTGRES["database"])
    p_complete.add_argument("--skip-index", action="store_true")
    p_complete.add_argument("--skip-validation", action="store_true")
    p_complete.add_argument("--low-memory", action="store_true")
    p_complete.add_argument("--parallel", type=str2bool, nargs="?", const=True,
                            default=DEFAULT_PARALLEL, help="Multithread para Postgres (True/False)")
    p_complete.add_argument("--clean", action="store_true")
    p_complete.add_argument("--workers", type=int)
    p_complete.add_argument("--skip-download", action="store_true",
                            help="Não baixa os arquivos, apenas executa as etapas do banco de dados")
    p_complete.add_argument("--skip-views", action="store_true",
                            help="Não cria Materialized Views ao final")

    args = parser.parse_args()

    try:
        set_log_file(_resolve_log_file_path(args.log_file))

        if args.command == "get-availables":
            data = CNPJDataScraper()
            print_log(data.get_availables(), level="docs", time=False)

        elif args.command == "get-latest":
            data = CNPJDataScraper()
            print_log(data.get_latest(), level="docs", time=False)

        elif args.command == "get-urls":
            data = CNPJDataScraper()
            urls = data.get_metadata(month_year=args.month)
            for info in urls.values():
                print_log(info["file_url"], level="web", time=False)

        elif args.command == "download":
            month = _resolve_month(args)
            state, server = _init_observability(args, month)
            try:
                dm = CNPJDownloadManager(
                    month_year=month,
                    concurrents=args.workers,
                    clean=args.clean,
                    download_dir=args.download_dir,
                )
                run_step(
                    state, STEP_DOWNLOAD,
                    lambda: dm.start_download_queue(on_progress=_download_progress(state)),
                    metadata_fn=lambda _: _download_summary(_download_details(dm))
                )
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                raise
            else:
                if state:
                    state.pipeline_finished()
            finally:
                _shutdown_dashboard(server, args)

        elif args.command == "db":
            # `dead-letter` is maintenance over parked batches: no state, no
            # stats, no orchestrator — list or re-attempt the COPYs and leave.
            if args.db_command == "dead-letter":
                from .db.dead_letter import retry_dead_letters, show_dead_letters
                if args.retry:
                    result = retry_dead_letters(_db_config(args.db_name),
                                                dead_letter_dir=args.dir)
                    if result["failed"]:
                        sys.exit(1)
                else:
                    show_dead_letters(dead_letter_dir=args.dir)
                return

            # `views refresh` is recurring maintenance, not a build step: it
            # opens no state and records no run — re-running it is always
            # valid and there is nothing to resume.
            is_refresh = (args.db_command == "views"
                          and getattr(args, "views_command", None) == "refresh")
            month = None if is_refresh else _resolve_month(args)
            db_name = args.db_name
            state, server = (None, None) if is_refresh else _init_observability(args, month, db_name)
            _record_stats_start(state, db_name)
            records = None
            try:
                # Views command (own subcommand).
                if args.db_command == "views":
                    metrics = run_orchestrator(
                        command=f"views-{args.views_command}",
                        db_name=db_name,
                        concurrent=getattr(args, "concurrent", False),
                        state=state if args.views_command == "create" else None,
                    )
                else:
                    metrics = run_orchestrator(
                        command=args.db_command,
                        db_name=db_name,
                        month_year=getattr(args, "month", None),
                        files_dir=getattr(args, "download_dir", None),
                        skip_indexes=getattr(args, "skip_index", False),
                        skip_validation=getattr(args, "skip_validation", False),
                        low_memory=getattr(args, "low_memory", DEFAULT_LOW_MEMORY),
                        parallel=getattr(args, "parallel", DEFAULT_PARALLEL),
                        only_data=getattr(args, "only_data", False),
                        state=state,
                    )
                records = (metrics or {}).get("records_inserted")
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                _record_stats_finish(state, db_name, "failed", records=records, error=str(exc))
                raise
            else:
                if state:
                    state.pipeline_finished()
                _record_stats_finish(state, db_name, "completed", records=records)
            finally:
                _shutdown_dashboard(server, args)

        elif args.command == "complete":
            month = _resolve_month(args)
            db_name = args.db_name
            state, server = _init_observability(args, month, db_name)
            _record_stats_start(state, db_name)
            records = None
            downloads = None
            try:
                if not args.skip_download:
                    dm = CNPJDownloadManager(
                        month_year=month,
                        concurrents=args.workers,
                        clean=args.clean,
                        download_dir=args.download_dir,
                    )
                    run_step(
                        state, STEP_DOWNLOAD,
                        lambda: dm.start_download_queue(on_progress=_download_progress(state)),
                        metadata_fn=lambda _: _download_summary(_download_details(dm))
                    )
                    downloads = _download_details(dm)
                    if month is None:
                        # Keep state, orchestrator and files_dir on the exact
                        # month the download manager resolved.
                        month = dm.month_year

                metrics = run_orchestrator(
                    command="load",
                    db_name=db_name,
                    month_year=month,
                    files_dir=_complete_files_dir(getattr(args, "download_dir", None), month),
                    skip_indexes=getattr(args, "skip_index", False),
                    skip_validation=args.skip_download or getattr(args, "skip_validation", False),
                    low_memory=getattr(args, "low_memory", DEFAULT_LOW_MEMORY),
                    parallel=args.parallel,
                    state=state,
                )
                records = (metrics or {}).get("records_inserted")

                if not args.skip_views:
                    run_orchestrator(
                        command="views-create",
                        db_name=db_name,
                        state=state,
                    )
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                _record_stats_finish(state, db_name, "failed", records=records,
                                     downloads=downloads, error=str(exc))
                raise
            else:
                if state:
                    state.pipeline_finished()
                _record_stats_finish(state, db_name, "completed", records=records,
                                     downloads=downloads)
            finally:
                _shutdown_dashboard(server, args)

    except ValueError as e:
        # Validation-style failures (bad period, folder missing, ZIP validation)
        # must be visible to cron/CI: log in Portuguese, exit nonzero.
        print_log(str(e), level="error", time=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
