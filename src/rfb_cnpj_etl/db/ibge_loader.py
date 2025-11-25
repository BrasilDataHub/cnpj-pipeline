"""
Carga das tabelas de referência IBGE (região, estado, cidade) a partir dos CSVs locais.
"""

import sqlite3
from typing import Dict, List, Optional

import psycopg2

from ..utils.ibge_lookup import IBGELookup
from ..utils.logger import print_log


def _insert_many_sqlite(conn, table: str, columns: List[str], rows: List[List]):
    if not rows:
        return
    placeholders = ",".join("?" for _ in columns)
    col_names = ",".join(f'"{c}"' for c in columns)
    conn.executemany(f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders});', rows)


def _insert_many_postgres(conn, table: str, columns: List[str], rows: List[List]):
    if not rows:
        return
    placeholders = ",".join("%s" for _ in columns)
    col_names = ",".join(f'"{c}"' for c in columns)
    cur = conn.cursor()
    cur.executemany(f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders});', rows)
    cur.close()


def carregar_tabelas_ibge(engine: str, db_path: Optional[str] = None, postgres_config: Optional[Dict] = None):
    """
    Popular as tabelas regiao/estado/cidade antes da carga principal.
    """
    lookup = IBGELookup()
    referencias = lookup.get_reference_rows()

    if not referencias:
        print_log(
            "TABELAS IBGE NÃO CARREGADAS (CSVs ausentes ou inválidos). As colunas IBGE serão preenchidas como NULL.",
            level="warning"
        )
        return

    conn = None
    try:
        if engine == "sqlite":
            if not db_path:
                raise ValueError("db_path é obrigatório para engine sqlite.")
            conn = sqlite3.connect(db_path)
        elif engine == "postgres":
            if not postgres_config:
                raise ValueError("postgres_config é obrigatório para engine postgres.")
            conn = psycopg2.connect(**postgres_config)
        else:
            raise ValueError(f"Engine não suportada: {engine}")

        cur = conn.cursor()
        cur.execute('DELETE FROM "cidade";')
        cur.execute('DELETE FROM "estado";')
        cur.execute('DELETE FROM "regiao";')
        cur.close()

        insert_sql = _insert_many_sqlite if engine == "sqlite" else _insert_many_postgres

        insert_sql(conn, "regiao", ["cod_regiao_ibge", "sigla_regiao", "nome_regiao"],
                   referencias["regiao"])
        insert_sql(conn, "estado",
                   ["cod_estado_ibge", "sigla_uf", "nome_estado", "latitude", "longitude", "cod_regiao_ibge"],
                   referencias["estado"])
        insert_sql(conn, "cidade",
                   ["cod_cidade_ibge", "nome_cidade", "latitude", "longitude", "capital", "cod_estado_ibge",
                    "cod_municipio", "ddd", "fuso_horario"],
                   referencias["cidade"])

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
