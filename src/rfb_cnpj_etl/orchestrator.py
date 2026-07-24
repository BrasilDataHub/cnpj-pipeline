# orchestrator.py

"""
Orquestração da carga de dados
"""

import os
from typing import Optional
from .cnpj_data import CNPJDataScraper
from .db import PostgresBuilder, run_postgres_loader, carregar_tabelas_ibge
from .utils.logger import print_log
from .utils.zip_metadata import validate_zip_files, estimate_total_lines_from_size
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
        concurrent: bool = False
):
    """
    Orquestração da carga no banco de dados PostgreSQL.

    Comandos disponíveis:
        - init: Cria schema e tabelas
        - load: Carrega dados (com ou sem --only-data)
        - patch: Aplica correções estáticas na base
        - logged: Converte tabelas UNLOGGED para LOGGED (durabilidade)
        - pk: Adiciona chaves primárias
        - index: Cria todos os índices (básicos + avançados)
        - fk: Cria chaves estrangeiras
        - views-create: Cria/recria Materialized Views
        - views-refresh: Atualiza Materialized Views

    :params:
        command: comando a ser executado.
        db_name: nome do banco de dados Postgres.
        month_year: mês e ano a ser carregado ("MM/AAAA").
        files_dir: diretório com os arquivos CSV.
        skip_indexes: se deve pular a criação de índices.
        skip_validation: se deve pular a validação dos arquivos.
        parallel: se deve usar threads para processamento.
        low_memory: se deve usar baixa memória para processamento.
        only_data: se True, carrega apenas dados sem executar patch/pk/index/fk.
        concurrent: se True, usa REFRESH CONCURRENTLY para views (requer índice único).
    """
    print_log("INICIANDO TAREFAS DO BANCO DE DADOS...", level="start")

    estimated_lines = None

    # se for comando de carga, preparar diretórios e arquivos
    if command == "load":
        data = CNPJDataScraper()

        if month_year is None:
            month_year = data.get_latest()

        if files_dir is None:
            mm, aaaa = month_year.split('/')
            folder = f"{aaaa}-{mm}"
            files_dir = os.path.join(DOWNLOAD_DIR, folder)

        # validar dos arquivos na pasta
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

        # estimar linhas totais para controlar o progresso
        estimated_lines = estimate_total_lines_from_size(files_dir)

    # configurar conexão PostgreSQL
    postgres_config = POSTGRES.copy()
    if db_name and db_name != POSTGRES["database"]:
        postgres_config["database"] = db_name
    builder = PostgresBuilder(config=postgres_config)

    # =========================================================================
    # ETAPA 1: Inicialização do schema (init ou load)
    # =========================================================================
    if command in ("init", "load"):
        builder.initialize_schema()
        carregar_tabelas_ibge(postgres_config=postgres_config)

    # =========================================================================
    # ETAPA 2: Carga dos dados (load)
    # =========================================================================
    if command == "load":
        run_postgres_loader(
            files_dir=files_dir,
            postgres_config=postgres_config,
            total_records=estimated_lines,
            parallel=parallel,
            low_memory=low_memory
        )

    # =========================================================================
    # ETAPA 3: Aplicar correções estáticas (patch ou load sem --only-data)
    # =========================================================================
    if command == "patch":
        builder.apply_patches()
    elif command == "load" and not only_data:
        builder.apply_patches()

    # =========================================================================
    # ETAPA 3.5: Converter tabelas para LOGGED (logged ou load sem --only-data)
    # Antes de PKs/índices: com menos relações, a reescrita com WAL é menor.
    # UNLOGGED não sobrevive a crash — sem esta etapa, um crash trunca a base.
    # =========================================================================
    if command == "logged":
        builder.set_tables_logged()
    elif command == "load" and not only_data:
        builder.set_tables_logged()

    # =========================================================================
    # ETAPA 4: Criar chaves primárias (pk ou load sem --only-data)
    # =========================================================================
    if command == "pk":
        builder.add_primary_keys()
    elif command == "load" and not only_data:
        builder.add_primary_keys()

    # =========================================================================
    # ETAPA 5: Criar índices (index ou load sem --only-data e sem --skip-index)
    # =========================================================================
    if command == "index":
        builder.create_indexes()
    elif command == "load" and not only_data and not skip_indexes:
        builder.create_indexes()

    # =========================================================================
    # ETAPA 6: Criar chaves estrangeiras (fk ou load sem --only-data)
    # =========================================================================
    if command == "fk":
        builder.enable_foreign_keys()
    elif command == "load" and not only_data:
        builder.enable_foreign_keys()

    # =========================================================================
    # ETAPA 7: Materialized Views (comandos opcionais)
    # =========================================================================
    if command == "views-create":
        builder.create_materialized_views()

    if command == "views-refresh":
        builder.refresh_materialized_views(concurrent=concurrent)

    print_log(f"EXECUÇÃO FINALIZADA | POSTGRES | {month_year or command}", level="done")
