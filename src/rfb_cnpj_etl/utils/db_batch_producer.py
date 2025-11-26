# utils/db_batch_producer.py

import gc
from queue import Queue, Full, Empty
from pathlib import Path
import zipfile
import csv
import time
from io import TextIOWrapper
from typing import Optional, List, Dict, Callable
from threading import Thread
from .logger import print_log
from ..db.schema import SCHEMA
from ..config import BATCH_SIZE, BATCH_RATIO
from ..utils.db_transformers import transform_batch, sanitize_for_postgres, IBGE_LOOKUP


def get_targets_from_zip_name(zip_name: str) -> List[Dict]:
    zip_stem = Path(zip_name).stem.rstrip('0123456789')
    skip_prefixes = set()

    targets = []
    for table_name, definition in SCHEMA.items():
        if definition['source_file_stem'].lower() == zip_stem.lower():
            columns = [col[0] for col in definition['columns']]
            targets.append({'name': table_name, 'columns': columns})

    if not targets:
        print_log(f"ARQUIVO ZIP DESCONHECIDO IGNORADO: {zip_name}", level="warning")
    return targets


def _process_zip_file(zip_file: Path, insertion_queue: Queue,
                      sanitizer_func: Callable, low_memory: bool = False, ):
    try:
        targets = get_targets_from_zip_name(zip_file.name)
        if not targets:
            return

        batches = {t['name']: [] for t in targets}
        columns_map = {t['name']: t['columns'] for t in targets}

        estab_cols_map = {}
        is_estab_file = any(t['name'] in ['estabelecimento', 'estabelecimento_cnae_sec'] for t in targets)
        if is_estab_file:
            # IMPORTANTE: Usar índices do arquivo CSV ORIGINAL, não do SCHEMA!
            # O SCHEMA inclui colunas computadas (cnpj_completo, cod_regiao_ibge, etc.)
            # que NÃO existem no arquivo CSV de origem da RFB.
            # Estrutura do CSV original de Estabelecimentos (29 colunas):
            # 0:cnpj_basico, 1:cnpj_ordem, 2:cnpj_dv, 3:matriz_filial, 4:nome_fantasia,
            # 5:cod_situacao_cadastral, 6:data_situacao_cadastral, 7:cod_motivo_situacao_cadastral,
            # 8:nome_cidade_exterior, 9:cod_pais, 10:data_inicio_atividade, 11:cod_cnae_principal,
            # 12:cod_cnae_secundario, 13:tipo_logradouro, 14:logradouro, 15:numero,
            # 16:complemento, 17:bairro, 18:cep, 19:uf, 20:cod_municipio, 21:ddd_telefone_1,
            # 22:telefone_1, 23:ddd_telefone_2, 24:telefone_2, 25:ddd_fax, 26:fax, 27:email,
            # 28:situacao_especial, 29:data_situacao_especial
            estab_cols_map = {
                'cnpj_basico': 0,
                'cnpj_ordem': 1,
                'cnpj_dv': 2,
                'data_inicio_atividade': 10,
                'cnae_sec': 12,  # cod_cnae_secundario
                'uf': 19,
                'cod_municipio': 20,
            }

        with zipfile.ZipFile(zip_file, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                try:
                    with zip_ref.open(file_info.filename) as raw_file:
                        reader = csv.reader(TextIOWrapper(raw_file, encoding="latin1"), delimiter=';')
                        for row in reader:
                            for target in targets:
                                table_name = target['name']

                                if table_name == 'estabelecimento_cnae_sec':
                                    cnaes_secundarios = row[estab_cols_map['cnae_sec']].split(',')
                                    # Faz lookup IBGE uma vez por linha de estabelecimento
                                    cod_municipio = row[estab_cols_map['cod_municipio']] if estab_cols_map['cod_municipio'] < len(row) else None
                                    uf = row[estab_cols_map['uf']] if estab_cols_map['uf'] < len(row) else None
                                    data_inicio = row[estab_cols_map['data_inicio_atividade']] if estab_cols_map['data_inicio_atividade'] < len(row) else None
                                    cod_regiao_ibge, cod_estado_ibge, cod_cidade_ibge = IBGE_LOOKUP.lookup_codigos(cod_municipio, uf)

                                    for cnae in cnaes_secundarios:
                                        cnae_limpo = cnae.strip()
                                        if cnae_limpo:
                                            # Propaga dados desnormalizados do estabelecimento para cnae_sec
                                            # Ordem conforme SCHEMA['estabelecimento_cnae_sec']['columns']
                                            new_row = [
                                                row[estab_cols_map['cnpj_basico']],  # cnpj_basico
                                                row[estab_cols_map['cnpj_ordem']],   # cnpj_ordem
                                                row[estab_cols_map['cnpj_dv']],      # cnpj_dv
                                                '',                                  # cnpj_completo (computado no transform_batch)
                                                cnae_limpo,                          # cod_cnae
                                                cod_regiao_ibge,                     # cod_regiao_ibge (desnormalizado)
                                                cod_estado_ibge,                     # cod_estado_ibge (desnormalizado)
                                                cod_cidade_ibge,                     # cod_cidade_ibge (desnormalizado)
                                                data_inicio,                         # data_inicio_atividade (desnormalizado)
                                            ]
                                            batches[table_name].append(new_row)
                                elif table_name == 'estabelecimento':
                                    # Inserir placeholder para cnpj_completo na posição 3
                                    # O CSV original tem 30 colunas, mas o SCHEMA espera cnpj_completo na posição 3
                                    # A ordem é: cnpj_basico(0), cnpj_ordem(1), cnpj_dv(2), [cnpj_completo], matriz_filial(3->4), ...
                                    new_row = list(row)
                                    new_row.insert(3, '')  # Placeholder para cnpj_completo (será computado no transform_batch)
                                    batches[table_name].append(new_row)
                                else:
                                    batches[table_name].append(row)

                            for table_name, batch_list in batches.items():
                                ratio = BATCH_RATIO.get(table_name, 1.0)
                                batch_size = int(BATCH_SIZE * ratio)
                                if len(batch_list) >= batch_size:
                                    item = {
                                        "table": table_name,
                                        "columns": columns_map[table_name],
                                        "rows": batch_list,
                                        "filename": str(zip_file)
                                    }
                                    transformed_rows = transform_batch(item, sanitizer_func)
                                    item["rows"] = transformed_rows

                                    if transformed_rows:
                                        # Usa put com timeout para evitar race condition
                                        # entre full() e put()
                                        while True:
                                            try:
                                                insertion_queue.put(item, timeout=0.1)
                                                break
                                            except Full:
                                                continue

                                    batches[table_name] = []

                        for table_name, batch_list in batches.items():
                            if batch_list:
                                item = {
                                    "table": table_name,
                                    "columns": columns_map[table_name],
                                    "rows": batch_list,
                                    "filename": str(zip_file)
                                }
                                # CHAMADA CORRIGIDA para usar a variável 'sanitizer'
                                transformed_rows = transform_batch(item, sanitizer_func)
                                item["rows"] = transformed_rows

                                if transformed_rows:
                                    # Usa put com timeout para evitar race condition
                                    while True:
                                        try:
                                            insertion_queue.put(item, timeout=0.1)
                                            break
                                        except Full:
                                            continue
                except Exception as e:
                    print_log(f"Erro ao ler {file_info.filename} em {zip_file.name}: {e}", level="error")

    except Exception as e:
        print_log(f"Erro ao abrir {zip_file.name}: {e}", level="error")

    finally:
        if low_memory:
            gc.collect()


def produce_batches(files_dir: str, insertion_queue: Queue, num_workers: Optional[int] = None,
                    parallel: bool = False, low_memory: bool = False):
    zip_files = sorted(Path(files_dir).glob("*.zip"))

    sanitizer = sanitize_for_postgres

    if parallel:
        # Usar um número limitado de threads de produção (máximo 4)
        # para evitar contenção excessiva de I/O e na fila
        max_producer_threads = min(4, len(zip_files))

        # Fila de arquivos a processar
        file_queue: Queue = Queue()
        for zip_file in zip_files:
            file_queue.put(zip_file)

        def producer_worker():
            while True:
                try:
                    zip_file = file_queue.get_nowait()
                except Empty:
                    break
                _process_zip_file(zip_file, insertion_queue, sanitizer, low_memory)
                file_queue.task_done()

        threads = []
        for _ in range(max_producer_threads):
            t = Thread(target=producer_worker)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()
    else:
        for zip_file in zip_files:
            _process_zip_file(zip_file, insertion_queue, sanitizer, low_memory)

    # sentinelas são inseridas pelo consumidor chamador
