"""
Loading of the IBGE reference tables (region, state, city) from the local CSVs.
"""

from typing import Dict, List, Optional

import psycopg2

from ..utils.ibge_lookup import IBGELookup
from ..utils.logger import print_log

def _insert_many_postgres(conn, table: str, columns: List[str], rows: List[List]):
    if not rows:
        return
    placeholders = ",".join("%s" for _ in columns)
    col_names = ",".join(f'"{c}"' for c in columns)
    cur = conn.cursor()
    cur.executemany(f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders});', rows)
    cur.close()


def load_ibge_tables(postgres_config: Optional[Dict] = None):
    """
    Populate the regiao/estado/cidade tables before the main load.
    """
    lookup = IBGELookup()
    reference_rows = lookup.get_reference_rows()

    if not reference_rows:
        print_log(
            "TABELAS IBGE NÃO CARREGADAS (CSVs ausentes ou inválidos). As colunas IBGE serão preenchidas como NULL.",
            level="warning"
        )
        return

    conn = None
    try:
        if not postgres_config:
            raise ValueError("postgres_config é obrigatório para engine postgres.")
        conn = psycopg2.connect(**postgres_config)

        cur = conn.cursor()
        cur.execute('DELETE FROM "ibge_cidade";')
        cur.execute('DELETE FROM "ibge_estado";')
        cur.execute('DELETE FROM "ibge_regiao";')
        cur.close()

        _insert_many_postgres(conn, "ibge_regiao", ["cod_regiao_ibge", "sigla_regiao", "nome_regiao"],
                              reference_rows["ibge_regiao"])
        _insert_many_postgres(conn, "ibge_estado",
                              ["cod_estado_ibge", "sigla_uf", "nome_estado", "latitude", "longitude", "cod_regiao_ibge"],
                              reference_rows["ibge_estado"])
        _insert_many_postgres(conn, "ibge_cidade",
                              ["cod_cidade_ibge", "nome_cidade", "latitude", "longitude", "capital", "cod_estado_ibge",
                               "cod_municipio", "ddd", "fuso_horario"],
                              reference_rows["ibge_cidade"])

        conn.commit()
        print_log("TABELAS IBGE ATUALIZADAS", level="success")
    except Exception as exc:
        if conn:
            conn.rollback()
        print_log(f"ERRO AO CARREGAR TABELAS IBGE: {exc}", level="error")
        raise
    finally:
        if conn:
            conn.close()
