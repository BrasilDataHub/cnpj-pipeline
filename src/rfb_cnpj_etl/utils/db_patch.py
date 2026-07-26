# utils/db_patch.py

"""
Applies static fixes to the database.
"""

from ..utils.logger import print_log


def apply_static_fixes(conn):
    """
    Applies static fixes to the database.

    Optimized execution order:
    1. INSERTs of missing reference data (countries, qualifications, reasons)
    2. UPDATEs for data normalization
    3. DELETEs to remove duplicates/inconsistencies
    4. VACUUM ANALYZE to reclaim space and refresh statistics

    :params:
        conn: database connection.
    """
    try:
        print_log("APLICANDO CORREÇÕES NA BASE DE DADOS...", level="task")
        cur = conn.cursor()

        # =====================================================================
        # PHASE 1: INSERTs of missing reference data
        # Run BEFORE any UPDATE/DELETE to avoid FK violations
        # =====================================================================

        print_log("  -> Inserindo dados de referência faltantes...", level="docs")

        # Extra countries that are not present in the original RFB file
        cur.execute("""
                    INSERT INTO pais (cod_pais, nome_pais)
                    VALUES ('008', 'ABU DHABI'),
                           ('009', 'DIRCE'),
                           ('015', 'ALAND, ILHAS'),
                           ('150', 'JERSEY'),
                           ('151', 'CANARIAS, ILHAS'),
                           ('200', 'CURACAO'),
                           ('321', 'GUERNSEY'),
                           ('359', 'MAN, ILHA DE'),
                           ('367', 'INGLATERRA'),
                           ('393', 'JERSEY'),
                           ('449', 'MACEDONIA (ANTIGA REP. IUGOSLAVA)'),
                           ('452', 'MADEIRA, ILHA DA'),
                           ('498', 'MOLDAVIA'),
                           ('578', 'PALESTINA'),
                           ('678', 'SAO TOME E PRINCIPE'),
                           ('699', 'SAO MARTINHO, ILHA DE (PARTE HOLANDESA)'),
                           ('737', 'SERVIA'),
                           ('994', 'AZERBAIJAO')
                    ON CONFLICT (cod_pais) DO NOTHING;
                    """)

        # Missing partner qualification
        cur.execute("""
                    INSERT INTO qualificacao_socio (cod_qualificacao, nome_qualificacao)
                    VALUES ('36', 'Gerente-Delegado')
                    ON CONFLICT (cod_qualificacao) DO NOTHING;
                    """)

        # Missing registration status reasons
        cur.execute("""
                    INSERT INTO motivo (cod_motivo, nome_motivo)
                    VALUES ('32', 'DECURSO DE PRAZO DE INTERRUPCAO TEMPORARIA'),
                           ('81', 'SOLICITACAO DA ADMINISTRACAO TRIBUTARIA MUNICIPAL/ESTADUAL - SC'),
                           ('93', 'CNPJ - TITULAR BAIXADO')
                    ON CONFLICT (cod_motivo) DO NOTHING;
                    """)

        conn.commit()

        # =====================================================================
        # PHASE 2: UPDATEs for data normalization
        # =====================================================================

        print_log("  -> Normalizando dados...", level="docs")

        # Normalizes cod_pais with zero padding (before clearing the invalid '0')
        cur.execute("""
                    UPDATE estabelecimento
                    SET cod_pais = LPAD(cod_pais, 3, '0')
                    WHERE cod_pais IS NOT NULL
                      AND LENGTH(TRIM(cod_pais)) = 2;
                    """)

        # Removes cod_pais = '0' (invalid code) - after the extra countries are inserted
        cur.execute("UPDATE estabelecimento SET cod_pais = NULL WHERE cod_pais = '0';")

        # Normalizes an empty cod_porte to '00' (not informed)
        cur.execute("UPDATE empresa SET cod_porte = '00' WHERE cod_porte = '';")

        conn.commit()

        # =====================================================================
        # PHASE 2.5: safety net for orphan cod_pais
        # ---------------------------------------------------------------------
        # The fixed list in PHASE 1 covers the extra codes already known, but
        # the RFB publishes in `estabelecimento`/`socio` legacy codes that are
        # not present in the PAISCSV of the same month — and the set changes
        # from month to month (in 07/2026: 042, 693 and 755, all of them gaps
        # in the sequence of the domain table). Each orphan code aborts the
        # creation of fk_estabelecimento_3 or fk_socio_2, silently leaving the
        # database without those two FKs.
        #
        # This block absorbs any new code automatically, without requiring
        # someone to edit the list above every month. It has to run AFTER the
        # LPAD of PHASE 2 — before it, 2-digit codes would go in without padding.
        # =====================================================================

        print_log("  -> Absorvendo códigos de país órfãos...", level="docs")

        inserted_countries = 0
        for table in ("estabelecimento", "socio"):
            cur.execute(f"""
                        INSERT INTO pais (cod_pais, nome_pais)
                        SELECT DISTINCT o.cod_pais, 'CODIGO NAO CONSTANTE NA TABELA RFB'
                          FROM {table} o
                         WHERE o.cod_pais IS NOT NULL
                           AND NOT EXISTS (
                               SELECT 1 FROM pais p WHERE p.cod_pais = o.cod_pais
                           )
                        ON CONFLICT (cod_pais) DO NOTHING;
                        """)
            inserted_countries += cur.rowcount or 0

        # Always report: a new orphan code is a sign of a change at the source,
        # and the silence here is what hid the problem until 07/2026.
        if inserted_countries:
            print_log(
                f"  -> {inserted_countries} código(s) de país órfão(s) absorvido(s) "
                f"— ausentes do PAISCSV da RFB",
                level="warning"
            )

        conn.commit()

        # =====================================================================
        # PHASE 3: DELETEs to remove duplicates/inconsistencies
        # =====================================================================

        print_log("  -> Removendo duplicatas e inconsistências...", level="docs")

        # Removes duplicate empresa rows, keeping the one with razao_social filled in
        dedup_delete_query = """
            DELETE FROM empresa
            WHERE ctid IN (
                SELECT ctid
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (
                               PARTITION BY cnpj_basico
                               ORDER BY CASE
                                   WHEN razao_social IS NOT NULL AND TRIM(razao_social) <> ''
                                   THEN 0 ELSE 1
                               END, ctid
                           ) as rn
                    FROM empresa
                ) t
                WHERE t.rn > 1
            );
        """
        cur.execute(dedup_delete_query)

        # Removes orphan simples records that cause FK problems
        cur.execute("""
                    DELETE FROM simples
                    WHERE cnpj_basico IN (
                        '24417449', '24539162', '30721933', '30728066',
                        '30760363', '30847991', '30857441', '30886793', '30972017'
                    );
                    """)

        conn.commit()

        # =====================================================================
        # PHASE 4: VACUUM ANALYZE to reclaim space after the massive DELETEs
        # =====================================================================

        print_log("  -> Executando VACUUM ANALYZE nas tabelas modificadas...", level="docs")

        # autocommit is required for VACUUM
        old_autocommit = conn.autocommit
        conn.autocommit = True

        try:
            cur.execute("VACUUM (ANALYZE) empresa;")
            cur.execute("VACUUM (ANALYZE) estabelecimento;")
            cur.execute("VACUUM (ANALYZE) simples;")
        finally:
            conn.autocommit = old_autocommit

        print_log("CORREÇÕES APLICADAS", level="success")

    except Exception as e:
        print_log(f"ERRO AO APLICAR CORREÇÕES: {e}", level="error")
        raise
