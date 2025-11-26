# utils/db_patch.py

"""
Aplica correções estáticas na base de dados.
"""

from ..utils.logger import print_log


def apply_static_fixes(conn):
    """
    Aplica correções estáticas na base de dados.

    Ordem de execução otimizada:
    1. INSERTs de dados de referência faltantes (países, qualificações, motivos)
    2. UPDATEs para normalização de dados
    3. DELETEs para remoção de duplicatas/inconsistências
    4. VACUUM ANALYZE para recuperar espaço e atualizar estatísticas

    :params:
        conn: conexão com o banco de dados.
    """
    try:
        print_log("APLICANDO CORREÇÕES NA BASE DE DADOS...", level="task")
        cur = conn.cursor()

        # =====================================================================
        # FASE 1: INSERTs de dados de referência faltantes
        # Executar ANTES de qualquer UPDATE/DELETE para evitar FK violations
        # =====================================================================

        print_log("  -> Inserindo dados de referência faltantes...", level="docs")

        # Países extras que não constam no arquivo original da RFB
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

        # Qualificação de sócio faltante
        cur.execute("""
                    INSERT INTO qualificacao_socio (cod_qualificacao, nome_qualificacao)
                    VALUES ('36', 'Gerente-Delegado')
                    ON CONFLICT (cod_qualificacao) DO NOTHING;
                    """)

        # Motivos de situação cadastral faltantes
        cur.execute("""
                    INSERT INTO motivo (cod_motivo, nome_motivo)
                    VALUES ('32', 'DECURSO DE PRAZO DE INTERRUPCAO TEMPORARIA'),
                           ('81', 'SOLICITACAO DA ADMINISTRACAO TRIBUTARIA MUNICIPAL/ESTADUAL - SC'),
                           ('93', 'CNPJ - TITULAR BAIXADO')
                    ON CONFLICT (cod_motivo) DO NOTHING;
                    """)

        conn.commit()

        # =====================================================================
        # FASE 2: UPDATEs para normalização de dados
        # =====================================================================

        print_log("  -> Normalizando dados...", level="docs")

        # Normaliza cod_pais com padding de zeros (antes de limpar '0' inválido)
        cur.execute("""
                    UPDATE estabelecimento
                    SET cod_pais = LPAD(cod_pais, 3, '0')
                    WHERE cod_pais IS NOT NULL
                      AND LENGTH(TRIM(cod_pais)) = 2;
                    """)

        # Remove cod_pais = '0' (código inválido) - após países extras inseridos
        cur.execute("UPDATE estabelecimento SET cod_pais = NULL WHERE cod_pais = '0';")

        # Normaliza cod_porte vazio para '00' (não informado)
        cur.execute("UPDATE empresa SET cod_porte = '00' WHERE cod_porte = '';")

        conn.commit()

        # =====================================================================
        # FASE 3: DELETEs para remoção de duplicatas/inconsistências
        # =====================================================================

        print_log("  -> Removendo duplicatas e inconsistências...", level="docs")

        # Remove duplicatas de empresa, mantendo o registro com razao_social preenchida
        query_delete_duplicatas = """
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
        cur.execute(query_delete_duplicatas)

        # Remove registros órfãos de simples que causam problemas de FK
        cur.execute("""
                    DELETE FROM simples
                    WHERE cnpj_basico IN (
                        '24417449', '24539162', '30721933', '30728066',
                        '30760363', '30847991', '30857441', '30886793', '30972017'
                    );
                    """)

        conn.commit()

        # =====================================================================
        # FASE 4: VACUUM ANALYZE para recuperar espaço após DELETEs massivos
        # =====================================================================

        print_log("  -> Executando VACUUM ANALYZE nas tabelas modificadas...", level="docs")

        # Necessário autocommit para VACUUM
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
