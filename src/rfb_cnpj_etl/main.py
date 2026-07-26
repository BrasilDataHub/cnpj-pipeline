# main.py

import argparse
import os
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


def _observabilidade_parser() -> argparse.ArgumentParser:
    """Flags de estado, dashboard e webhooks, compartilhadas pelos subcomandos.

    Ficam num parser-pai (`parents=`) para que apareçam depois do subcomando
    (`etl.py complete --serve`), que é onde se espera encontrá-las.
    """
    p = argparse.ArgumentParser(add_help=False)
    grupo = p.add_argument_group("observabilidade")
    grupo.add_argument(
        "--force", "--force-restart", dest="force", action="store_true",
        help="Ignora o estado existente do período e reexecuta tudo do zero "
             "(o estado anterior é preservado como .bak-<timestamp>)"
    )
    grupo.add_argument(
        "--no-state", action="store_true",
        help="Desliga o checkpoint/retomada (não lê nem grava arquivo de estado)"
    )
    grupo.add_argument(
        "--reference-period", type=str,
        help="Período dos dados a que esta execução pertence (AAAA-MM ou MM/AAAA). "
             "Só é necessário em subcomandos sem --month (ex.: db init, db fk); "
             "sem ele, usa-se o estado mais recente"
    )
    grupo.add_argument(
        "--max-attempts", type=int, default=MAX_STEP_ATTEMPTS,
        help=f"Tentativas por etapa antes de exigir intervenção "
             f"(padrão: {MAX_STEP_ATTEMPTS}; 0 = ilimitado)"
    )
    grupo.add_argument(
        "--serve", action="store_true",
        help="Sobe o dashboard web somente leitura durante a execução"
    )
    grupo.add_argument(
        "--port", type=int, default=DASHBOARD_DEFAULT_PORT,
        help=f"Porta do dashboard (padrão: {DASHBOARD_DEFAULT_PORT})"
    )
    grupo.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Interface do dashboard (padrão: 127.0.0.1; use 0.0.0.0 em container)"
    )
    grupo.add_argument(
        "--dashboard-password", type=str, default=DASHBOARD_PASSWORD,
        help="Senha do dashboard (Basic Auth). Se omitida, uma é gerada e "
             "mostrada no log. Equivale a PIPELINE_DASHBOARD_PASSWORD"
    )
    grupo.add_argument(
        "--dashboard-user", type=str, default=DASHBOARD_USER,
        help=f"Usuário do dashboard (padrão: {DASHBOARD_USER})"
    )
    grupo.add_argument(
        "--no-auth", action="store_true",
        help="Serve o dashboard sem autenticação (só em rede confiável)"
    )
    grupo.add_argument(
        "--webhook-url", type=str,
        help="URL para notificações por etapa (tem prioridade sobre PIPELINE_WEBHOOK_URL)"
    )
    return p


def _resolver_mes(args) -> Optional[str]:
    """Determina o mês de referência ("MM/AAAA") uma única vez.

    Resolver aqui — e não dentro de cada componente — garante que estado,
    webhooks e download falem do mesmo período, e evita duas consultas à RFB.
    """
    # --reference-period é a declaração explícita e tem prioridade: existe
    # justamente para os subcomandos que não têm --month.
    explicito = getattr(args, "reference_period", None)
    if explicito:
        return explicito
    mes = getattr(args, "month", None)
    if mes:
        return mes
    if args.command in ("complete", "download"):
        try:
            return CNPJDataScraper().get_latest()
        except Exception as exc:
            print_log(f"NÃO FOI POSSÍVEL RESOLVER O MÊS MAIS RECENTE: {exc}", level="warning")
    return None


def _config_banco(db_name: Optional[str]) -> dict:
    """Configuração de conexão do banco alvo desta execução."""
    config = POSTGRES.copy()
    if db_name:
        config["database"] = db_name
    return config


def _iniciar_observabilidade(args, mes: Optional[str],
                             db_name: Optional[str] = None) -> Tuple[Optional[RunState], object]:
    """Prepara estado, webhooks e dashboard. Retorna (state, servidor_dashboard)."""
    notifier = WebhookNotifier.from_config(getattr(args, "webhook_url", None))

    if getattr(args, "no_state", False):
        if getattr(args, "serve", False):
            print_log("--serve IGNORADO: --no-state DESLIGA O ARQUIVO DE ESTADO", level="warning")
        return None, None

    periodo = normalize_reference_period(mes) or RunState.latest_period()
    if not periodo:
        print_log(
            "SEM PERÍODO DE REFERÊNCIA CONHECIDO — RASTREAMENTO DE ESTADO DESABILITADO "
            "(informe --month para habilitar)",
            level="warning"
        )
        return None, None

    # O coletor roda ao fim de cada etapa: mostra o banco crescendo durante a
    # carga em vez de um retrato congelado do início.
    config_banco = _config_banco(db_name)
    state = RunState.load_or_create(
        reference_period=periodo,
        force=getattr(args, "force", False),
        max_attempts=getattr(args, "max_attempts", MAX_STEP_ATTEMPTS),
        notifier=notifier,
        db_info_fn=(lambda: collect_database_info(config_banco)) if db_name else None,
    )
    print_log(f"ESTADO: {state.path}", level="folder")

    ambiente = collect_environment()
    banco = collect_database_info(config_banco) if db_name else {}
    state.set_environment(ambiente, banco)
    if ambiente:
        onde = ambiente.get("hostname") or "?"
        ip = ambiente.get("ip")
        print_log(
            f"AMBIENTE: {ambiente.get('runtime')} em {onde}"
            + (f" ({ip})" if ip else "")
            + f" · Python {ambiente.get('python')} · {ambiente.get('so')}",
            level="docs"
        )

    servidor = None
    if getattr(args, "serve", False):
        servidor = start_dashboard(
            state_path=state.path,
            port=getattr(args, "port", DASHBOARD_DEFAULT_PORT),
            host=getattr(args, "host", "127.0.0.1"),
            password=getattr(args, "dashboard_password", None),
            user=getattr(args, "dashboard_user", DASHBOARD_USER),
            auth=not getattr(args, "no_auth", False),
        )

    state.pipeline_started()
    return state, servidor


def _detalhe_downloads(dm: "CNPJDownloadManager") -> list:
    """Metadados dos arquivos baixados, para `files_downloaded_detail`.

    Lê tamanho e mtime do disco: é a única fonte que reflete o que de fato
    ficou gravado, inclusive quando o download foi retomado de execução
    anterior.
    """
    detalhe = []
    for caminho, url in zip(dm.file_paths, dm.file_urls):
        try:
            st = os.stat(caminho)
        except OSError:
            continue
        detalhe.append({
            "nome_arquivo": os.path.basename(caminho),
            "tamanho_bytes": st.st_size,
            "url_origem": url,
            "baixado_em": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
        })
    return detalhe


def _progresso_download(state):
    """Callback que publica o avanço do download no estado."""
    def _cb(baixados, total, arquivo):
        if state is None:
            return
        state.progress(
            STEP_DOWNLOAD,
            arquivos_baixados=int(baixados),
            arquivos_total=int(total),
            arquivos_restantes=int(total) - int(baixados),
            arquivo_atual=os.path.basename(arquivo or ""),
            percentual=round(baixados / total * 100, 1) if total else None,
        )
    return _cb


def _resumo_downloads(detalhe: list) -> dict:
    """Metadados da etapa de download, exibidos no dashboard."""
    return {
        "files_downloaded": len(detalhe),
        "total_bytes": sum(d.get("tamanho_bytes", 0) for d in detalhe),
    }


def _encerrar_dashboard(servidor, args) -> None:
    """Desliga o dashboard ao fim da execução.

    Encerra junto com o processo de propósito: manter o servidor no ar
    travaria execuções em cron. O estado final permanece no JSON, que pode ser
    reaberto depois com qualquer servidor estático.
    """
    if servidor is None:
        return
    try:
        servidor.shutdown()
        servidor.server_close()
    except Exception:
        pass
    print_log("DASHBOARD ENCERRADO (estado final permanece no arquivo JSON)", level="docs")


def _registrar_stats_inicio(state: Optional[RunState], db_name: str) -> None:
    """Grava `started_at` em pipeline_stats. Nunca interrompe o pipeline."""
    if state is None:
        return
    from .db import pipeline_stats
    conn = _conectar_stats(db_name)
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


def _registrar_stats_fim(
        state: Optional[RunState], db_name: str, status: str,
        records: Optional[int] = None, downloads: Optional[list] = None,
        error: Optional[str] = None,
) -> None:
    """Fecha a linha de pipeline_stats com os totais. Nunca interrompe."""
    if state is None:
        return
    from .db import pipeline_stats
    conn = _conectar_stats(db_name)
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


def _conectar_stats(db_name: str):
    """Conexão dedicada às estatísticas. Retorna None se o banco não responder."""
    import psycopg2
    config = POSTGRES.copy()
    if db_name:
        config["database"] = db_name
    try:
        return psycopg2.connect(**config)
    except Exception as exc:
        print_log(f"ESTATÍSTICAS INDISPONÍVEIS (sem conexão): {exc}", level="warning")
        return None


def main() -> None:
    obs = _observabilidade_parser()
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

    # db-views (subcomando com create e refresh)
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
            mes = _resolver_mes(args)
            state, servidor = _iniciar_observabilidade(args, mes)
            try:
                dm = CNPJDownloadManager(
                    month_year=mes,
                    concurrents=args.workers,
                    clean=args.clean,
                    download_dir=args.download_dir,
                )
                run_step(
                    state, STEP_DOWNLOAD,
                    lambda: dm.start_download_queue(on_progress=_progresso_download(state)),
                    metadata_fn=lambda _: _resumo_downloads(_detalhe_downloads(dm))
                )
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                raise
            else:
                if state:
                    state.pipeline_finished()
            finally:
                _encerrar_dashboard(servidor, args)

        elif args.command == "db":
            # `views refresh` é manutenção recorrente, não etapa de construção:
            # não abre estado nem registra execução — reexecutá-lo é sempre
            # válido e não há nada a retomar.
            eh_refresh = (args.db_command == "views"
                          and getattr(args, "views_command", None) == "refresh")
            mes = None if eh_refresh else _resolver_mes(args)
            db_name = args.db_name
            state, servidor = (None, None) if eh_refresh else _iniciar_observabilidade(args, mes, db_name)
            _registrar_stats_inicio(state, db_name)
            registros = None
            try:
                # Comando de views (subcomando próprio)
                if args.db_command == "views":
                    metricas = run_orchestrator(
                        command=f"views-{args.views_command}",
                        db_name=db_name,
                        concurrent=getattr(args, "concurrent", False),
                        state=state if args.views_command == "create" else None,
                    )
                else:
                    metricas = run_orchestrator(
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
                registros = (metricas or {}).get("records_inserted")
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                _registrar_stats_fim(state, db_name, "failed", records=registros, error=str(exc))
                raise
            else:
                if state:
                    state.pipeline_finished()
                _registrar_stats_fim(state, db_name, "completed", records=registros)
            finally:
                _encerrar_dashboard(servidor, args)

        elif args.command == "complete":
            mes = _resolver_mes(args)
            db_name = args.db_name
            state, servidor = _iniciar_observabilidade(args, mes, db_name)
            _registrar_stats_inicio(state, db_name)
            registros = None
            downloads = None
            try:
                if not args.skip_download:
                    dm = CNPJDownloadManager(
                        month_year=mes,
                        concurrents=args.workers,
                        clean=args.clean,
                        download_dir=args.download_dir,
                    )
                    run_step(
                        state, STEP_DOWNLOAD,
                        lambda: dm.start_download_queue(on_progress=_progresso_download(state)),
                        metadata_fn=lambda _: _resumo_downloads(_detalhe_downloads(dm))
                    )
                    downloads = _detalhe_downloads(dm)

                metricas = run_orchestrator(
                    command="load",
                    db_name=db_name,
                    month_year=mes,
                    files_dir=getattr(args, "download_dir", None),
                    skip_indexes=getattr(args, "skip_index", False),
                    skip_validation=args.skip_download or getattr(args, "skip_validation", False),
                    low_memory=getattr(args, "low_memory", DEFAULT_LOW_MEMORY),
                    parallel=args.parallel,
                    state=state,
                )
                registros = (metricas or {}).get("records_inserted")

                if not args.skip_views:
                    run_orchestrator(
                        command="views-create",
                        db_name=db_name,
                        state=state,
                    )
            except BaseException as exc:
                if state:
                    state.pipeline_failed(exc)
                _registrar_stats_fim(state, db_name, "failed", records=registros,
                                     downloads=downloads, error=str(exc))
                raise
            else:
                if state:
                    state.pipeline_finished()
                _registrar_stats_fim(state, db_name, "completed", records=registros,
                                     downloads=downloads)
            finally:
                _encerrar_dashboard(servidor, args)

    except ValueError as e:
        print_log(str(e), level="error", time=False)


if __name__ == "__main__":
    main()
