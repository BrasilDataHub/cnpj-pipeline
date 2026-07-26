# utils/db_transformers.py

"""
Data transformers.
"""

import csv
from io import BytesIO, StringIO
from datetime import datetime
from typing import List, Optional, Union, Callable, Any
from .ibge_lookup import IBGELookup

IBGE_LOOKUP = IBGELookup()


def compute_cnpj_completo(rows: List[List], columns: List[str]) -> List[List]:
    """
    Computes cnpj_completo by concatenating cnpj_basico + cnpj_ordem + cnpj_dv.

    This function must be called during the transformation process,
    BEFORE the COPY into the database.

    Args:
        rows: List of rows (each row is a list of values)
        columns: List with the column names in the corresponding order

    Returns:
        List of rows with cnpj_completo filled in

    Example:
        Input:  cnpj_basico='12345678', cnpj_ordem='0001', cnpj_dv='00'
        Output: cnpj_completo='12345678000100'
    """
    # Find the indexes of the CNPJ columns
    try:
        idx_basico = columns.index('cnpj_basico')
        idx_ordem = columns.index('cnpj_ordem')
        idx_dv = columns.index('cnpj_dv')
        idx_completo = columns.index('cnpj_completo')
    except ValueError:
        # If any column does not exist, return rows unmodified
        return rows

    new_rows = []
    for row in rows:
        row = list(row)
        # Ensure the values are strings and left-pad with zeros if needed
        basico = str(row[idx_basico] or '').zfill(8)
        ordem = str(row[idx_ordem] or '').zfill(4)
        dv = str(row[idx_dv] or '').zfill(2)

        # Concatenate and ensure exactly 14 characters
        cnpj_completo = (basico + ordem + dv)[:14].ljust(14, '0')
        row[idx_completo] = cnpj_completo
        new_rows.append(row)

    return new_rows


def sanitize_for_postgres(rows: List[List[Any]]) -> List[List[Any]]:
    """Sanitizes for databases with 'windows-1252' encoding."""
    cleaned_rows = []
    for row in rows:
        new_row = []
        for val in row:
            if isinstance(val, str):
                s = val.replace('\x00', '').strip()
                # Remove characters incompatible with windows-1252
                val = s.encode("windows-1252", "ignore").decode("windows-1252")
            new_row.append(val)
        cleaned_rows.append(new_row)
    return cleaned_rows


def normalize_numeric_br(
        rows: List[Union[list, tuple]],
        columns: List[str],
        target_columns: Optional[List[str]] = None
) -> List[List]:
    """Normalizes numeric values from the Brazilian format (1.234,56 → 1234.56)."""
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
    """Converts dates from the 'YYYYMMDD' format to 'YYYY-MM-DD'."""
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
    """Converts a list of rows into a CSV byte buffer for the Postgres COPY."""
    text_buffer = StringIO()
    writer = csv.writer(text_buffer, delimiter=';', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
    writer.writerows(rows)
    byte_buffer = BytesIO(text_buffer.getvalue().encode("windows-1252"))
    byte_buffer.seek(0)
    return byte_buffer


def transform_batch(item: dict, sanitizer_func: Callable) -> List:
    """
    Applies all the required transformations to a batch of data.

    Transformation order:
    1. Sanitization (cleanup of invalid characters)
    2. Normalization of dates and numeric values
    3. IBGE enrichment (for estabelecimento)
    4. Computation of cnpj_completo (for estabelecimento and estabelecimento_cnae_sec)
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
        rows = IBGE_LOOKUP.append_ibge_codes(rows, columns)
        # Compute the full CNPJ BEFORE the COPY
        if 'cnpj_completo' in columns:
            rows = compute_cnpj_completo(rows, columns)

    elif table == "estabelecimento_cnae_sec":
        # Normalizes data_inicio_atividade
        # Note: cnpj_completo is already computed in db_batch_producer.py for this table
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
