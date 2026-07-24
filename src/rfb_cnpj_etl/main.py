# main.py

import argparse
import os
from datetime import datetime
from pathlib import Path
from .orchestrator import run_orchestrator
from .cnpj_data import CNPJDataScraper, CNPJDownloadManager
from .utils.logger import print_log, set_log_file
from .config import DEFAULT_PARALLEL, DEFAULT_LOW_MEMORY, POSTGRES, BASE_DIR, DATA_DIR


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


def main() -> None:
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
    p_dl = sub.add_parser("download", help="Baixa ZIPs de um ou mais meses")
    p_dl.add_argument("--month", type=str, help="Mês no formato MM/AAAA (ex: 03/2025)")
    p_dl.add_argument("--clean", action="store_true", help="Remove arquivos antigos")
    p_dl.add_argument("--workers", type=int, help="Número de downloads simultâneos")
    p_dl.add_argument("--download-dir", type=str, help="Diretório para salvar os arquivos")

    # DB
    db_cmd = sub.add_parser("db", help="Comandos relacionados ao banco de dados")
    db_sub = db_cmd.add_subparsers(dest="db_command", required=True)

    # db-init
    p_init = db_sub.add_parser("init", help="Inicializa o banco de dados")
    p_init.add_argument("--db-name", type=str, help="Nome do banco Postgres", default=POSTGRES["database"])

    # db-load
    p_load = db_sub.add_parser("load", help="Carrega dados CSV para o banco")
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
    p_index = db_sub.add_parser("index", help="Cria índices no banco")
    p_index.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-patch
    p_patch = db_sub.add_parser("patch", help="Aplica correções estáticas na base de dados")
    p_patch.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-logged
    p_logged = db_sub.add_parser("logged", help="Converte tabelas UNLOGGED para LOGGED (durabilidade)")
    p_logged.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-pk
    p_pk = db_sub.add_parser("pk", help="Adiciona chaves primárias nas tabelas grandes")
    p_pk.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-fk
    p_fk = db_sub.add_parser("fk", help="Cria chaves estrangeiras no banco")
    p_fk.add_argument("--db-name", type=str, default=POSTGRES["database"])

    # db-views (subcomando com create e refresh)
    views_cmd = db_sub.add_parser("views", help="Comandos para Materialized Views")
    views_sub = views_cmd.add_subparsers(dest="views_command", required=True)

    # db views create
    p_views_create = views_sub.add_parser("create", help="Cria/recria as Materialized Views")
    p_views_create.add_argument("--db-name", type=str, default=POSTGRES["database"],
                                help="Nome do banco Postgres")

    # db views refresh
    p_views_refresh = views_sub.add_parser("refresh", help="Atualiza as Materialized Views")
    p_views_refresh.add_argument("--db-name", type=str, default=POSTGRES["database"],
                                 help="Nome do banco Postgres")
    p_views_refresh.add_argument("--concurrent", action="store_true",
                                 help="Usar REFRESH CONCURRENTLY (requer índice único)")

    # complete
    p_complete = sub.add_parser("complete", help="Baixa e carrega dados automaticamente")
    p_complete.add_argument("--month", type=str)
    p_complete.add_argument("--download-dir", type=str)
    p_complete.add_argument("--db-name", type=str, default=POSTGRES["database"])
    p_complete.add_argument("--skip-index", action="store_true")
    p_complete.add_argument("--skip-validation", action="store_true")
    p_complete.add_argument("--low-memory", action="store_true")
    p_complete.add_argument("--parallel", action="store_true")
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
            print_log(data.get_availabes(), level="docs", time=False)

        elif args.command == "get-latest":
            data = CNPJDataScraper()
            print_log(data.get_latest(), level="docs", time=False)

        elif args.command == "get-urls":
            data = CNPJDataScraper()
            urls = data.get_metadata(month_year=args.month)
            for info in urls.values():
                print_log(info["file_url"], level="web", time=False)

        elif args.command == "download":

            dm = CNPJDownloadManager(
                month_year=args.month,
                concurrents=args.workers,
                clean=args.clean,
                download_dir=args.download_dir,
            )
            dm.start_download_queue()

        elif args.command == "db":
            # Comando de views (subcomando próprio)
            if args.db_command == "views":
                run_orchestrator(
                    command=f"views-{args.views_command}",
                    db_name=args.db_name,
                    concurrent=getattr(args, "concurrent", False)
                )
            else:
                run_orchestrator(
                    command=args.db_command,
                    db_name=args.db_name,
                    month_year=getattr(args, "month", None),
                    files_dir=getattr(args, "download_dir", None),
                    skip_indexes=getattr(args, "skip_index", False),
                    skip_validation=getattr(args, "skip_validation", False),
                    low_memory=getattr(args, "low_memory", DEFAULT_LOW_MEMORY),
                    parallel=getattr(args, "parallel", DEFAULT_PARALLEL),
                    only_data=getattr(args, "only_data", False)
                )

        elif args.command == "complete":
            if not args.skip_download:
                dm = CNPJDownloadManager(
                    month_year=args.month,
                    concurrents=args.workers,
                    clean=args.clean,
                    download_dir=args.download_dir,
                )
                dm.start_download_queue()

            run_orchestrator(
                command="load",
                db_name=args.db_name,
                month_year=getattr(args, "month", None),
                files_dir=getattr(args, "download_dir", None),
                skip_indexes=getattr(args, "skip_index", False),
                skip_validation=args.skip_download or getattr(args, "skip_validation", False),
                low_memory=getattr(args, "low_memory", DEFAULT_LOW_MEMORY),
                parallel=args.parallel
            )
            if not args.skip_views:
                run_orchestrator(
                    command="views-create",
                    db_name=args.db_name
                )

    except ValueError as e:
        print_log(str(e), level="error", time=False)


if __name__ == "__main__":
    main()
