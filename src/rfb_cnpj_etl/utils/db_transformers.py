# utils/db_transformers.py

"""
Transformadores de dados.
"""

import csv
from io import BytesIO, StringIO
from datetime import datetime
from typing import List, Optional, Union, Callable, Any
from .ibge_lookup import IBGELookup

IBGE_LOOKUP = IBGELookup()


def compute_cnpj_completo(rows: List[List], columns: List[str]) -> List[List]:
    """
    Computa cnpj_completo concatenando cnpj_basico + cnpj_ordem + cnpj_dv.

    Esta função deve ser chamada durante o processo de transformação,
    ANTES do COPY para o banco de dados.

    Args:
        rows: Lista de linhas (cada linha é uma lista de valores)
        columns: Lista com os nomes das colunas na ordem correspondente

    Returns:
        Lista de linhas com cnpj_completo preenchido

    Exemplo:
        Input:  cnpj_basico='12345678', cnpj_ordem='0001', cnpj_dv='00'
        Output: cnpj_completo='12345678000100'
    """
    # Encontrar índices das colunas CNPJ
    try:
        idx_basico = columns.index('cnpj_basico')
        idx_ordem = columns.index('cnpj_ordem')
        idx_dv = columns.index('cnpj_dv')
        idx_completo = columns.index('cnpj_completo')
    except ValueError:
        # Se alguma coluna não existir, retorna rows sem modificação
        return rows

    new_rows = []
    for row in rows:
        row = list(row)
        # Garantir que os valores são strings e preencher com zeros à esquerda se necessário
        basico = str(row[idx_basico] or '').zfill(8)
        ordem = str(row[idx_ordem] or '').zfill(4)
        dv = str(row[idx_dv] or '').zfill(2)

        # Concatenar e garantir exatamente 14 caracteres
        cnpj_completo = (basico + ordem + dv)[:14].ljust(14, '0')
        row[idx_completo] = cnpj_completo
        new_rows.append(row)

    return new_rows


def sanitize_for_postgres(rows: List[List[Any]]) -> List[List[Any]]:
    """Sanitiza para bancos de dados com encoding 'windows-1252'."""
    cleaned_rows = []
    for row in rows:
        new_row = []
        for val in row:
            if isinstance(val, str):
                s = val.replace('\x00', '').strip()
                # Remove caracteres incompatíveis com windows-1252
                val = s.encode("windows-1252", "ignore").decode("windows-1252")
            new_row.append(val)
        cleaned_rows.append(new_row)
    return cleaned_rows


def normalize_numeric_br(
        rows: List[Union[list, tuple]],
        columns: List[str],
        target_columns: Optional[List[str]] = None
) -> List[List]:
    """Normaliza valores numéricos do formato brasileiro (1.234,56 → 1234.56)."""
    new_rows = []
    col_indexes = (
        list(range(len(columns))) if target_columns is None
        else [i for i, col in enumerate(columns) if col in target_columns]
    )
    for row in rows:
        row = list(row)
        for i in col_indexes:
            val = row[i]
            if isinstance(val, str) and "," in val and val.replace(",", "").replace(".", "").isdigit():
                row[i] = val.replace(".", "").replace(",", ".")
        new_rows.append(row)
    return new_rows


def normalize_dates(
        rows: List[Union[list, tuple]],
        columns: List[str],
        target_columns: Optional[List[str]] = None
) -> List[List]:
    """Converte datas do formato 'YYYYMMDD' para 'YYYY-MM-DD'."""
    if target_columns is None:
        date_columns = [i for i, col in enumerate(columns) if col.startswith("data_")]
    else:
        date_columns = [i for i, col in enumerate(columns) if col in target_columns]
    new_rows = []
    for row in rows:
        new_row = list(row)
        for i in date_columns:
            val = new_row[i]
            if isinstance(val, str):
                val = val.strip()
                if val in ("00000000", "", " ", "0"):
                    new_row[i] = None
                elif len(val) == 8 and val.isdigit():
                    try:
                        new_row[i] = datetime.strptime(val, "%Y%m%d").date()
                    except ValueError:
                        new_row[i] = None
        new_rows.append(new_row)
    return new_rows


def convert_rows_to_csv_buffer(rows: List[List[Union[str, int, float, None]]]) -> BytesIO:
    """Converte uma lista de linhas em um buffer de bytes CSV para o COPY do Postgres."""
    text_buffer = StringIO()
    writer = csv.writer(text_buffer, delimiter=';', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
    writer.writerows(rows)
    byte_buffer = BytesIO(text_buffer.getvalue().encode("windows-1252"))
    byte_buffer.seek(0)
    return byte_buffer


def transform_batch(item: dict, sanitizer_func: Callable) -> List:
    """
    Aplica todas as transformações necessárias a um lote de dados.

    Ordem de transformações:
    1. Sanitização (limpeza de caracteres inválidos)
    2. Normalização de datas e valores numéricos
    3. Enriquecimento IBGE (para estabelecimento)
    4. Computação de cnpj_completo (para estabelecimento e estabelecimento_cnae_sec)
    """
    table = item["table"]
    columns = item["columns"]
    rows = item["rows"]

    rows = sanitizer_func(rows)

    if table == "empresa":
        rows = normalize_numeric_br(rows, columns, ["capital_social"])

    elif table == "estabelecimento":
        rows = normalize_dates(rows, columns, [
            "data_situacao_cadastral", "data_inicio_atividade", "data_situacao_especial"
        ])
        rows = IBGE_LOOKUP.append_ibge_to_estabelecimentos(rows, columns)
        # Computar CNPJ completo ANTES do COPY
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "estabelecimento_cnae_sec":
        # Normaliza data_inicio_atividade
        # Nota: cnpj_completo já é computado no db_batch_producer.py para esta tabela
        if 'data_inicio_atividade' in columns:
            rows = normalize_dates(rows, columns, ["data_inicio_atividade"])

    elif table == "simples":
        rows = normalize_dates(
            rows, columns,
            ["data_opcao_simples", "data_exclusao_simples", "data_opcao_mei", "data_exclusao_mei"]
        )

    elif table == "socio":
        rows = normalize_dates(rows, columns, ["data_entrada_sociedade"])

    return rows
