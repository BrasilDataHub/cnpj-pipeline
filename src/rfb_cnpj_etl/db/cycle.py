# db/cycle.py

"""
O ciclo mensal blue/green: ``bootstrap``, ``cycle``, ``publish``, ``validate``
e ``rollback``.

O QUE MUDA
==========

Hoje o ETL abre com ``DROP TABLE CASCADE`` de todo o schema. Foi assim que a
rodada de 25/07/2026 abortou **apos 6h43 sobre um banco ja destruido**: nao
havia estado intermediario do qual voltar, porque o primeiro passo tinha
eliminado o anterior. A indisponibilidade por ciclo e de 3h30 a 6h.

No blue/green, a carga acontece em ``dados_<N>`` enquanto ``dados_<N-1>``
SERVE. A publicacao e uma troca de ``search_path`` — **menos de 5 segundos** —
e o rollback e a troca inversa.

AS TRES SALVAGUARDAS QUE FAZEM PARTE DO DESENHO
===============================================

1. **``REVOKE`` do schema vigente** (``schema_target.py``), aplicado ANTES de
   qualquer passo: um ``public.`` literal sobrevivente falha com
   ``permission denied`` em vez de corromper o que esta servindo.
2. **Gate de delta por ``row_hash``**: delta acima de ``MAX_DELTA_PCT`` ABORTA a
   publicacao. E a assinatura de arquivo-fonte truncado ou de bug de parsing —
   os dois casos produzem uma carga que parece completa.
3. **Retencao do N-1**, nunca apagado no mesmo ciclo. O GC e do N-2, no
   preflight.

IDEMPOTENCIA
============

``cycle`` reexecutado do zero e no-op: cada etapa pergunta se ja foi feita antes
de fazer. E o que separa "retomar" de "recomecar", e a diferenca entre perder
minutos e perder as 6h43.
"""

import os
from dataclasses import dataclass
from typing import Optional

from ..utils.logger import print_log
from . import schema_target as st

#: Delta acima disto ABORTA a publicacao. 25% e a fronteira entre "mes com
#: muitas aberturas" e "arquivo-fonte truncado": o delta mensal real da RFB fica
#: em unidades de por cento, e nunca foi medido — sera, a partir da primeira
#: carga com row_hash (a 4a pendencia de 05 §10).
MAX_DELTA_PCT = float(os.getenv("MAX_DELTA_PCT", "25"))

#: Tabela de estado do ciclo, no schema `meta`. Sobrevive a toda carga.
TABELA_CICLOS = "meta.ciclos"


class CicloAbortado(RuntimeError):
    """Um portao de qualidade reprovou. Nada foi publicado."""


@dataclass
class ResultadoDoGate:
    delta_pct: float
    linhas_novas: int
    linhas_removidas: int
    linhas_alteradas: int
    aprovado: bool
    detalhe: str = ""


def criar_tabela_de_ciclos(conn) -> None:
    """Estado do ciclo, uma linha por carga. Idempotente."""
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{st.SCHEMA_META}";')
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABELA_CICLOS} (
                load_id           TEXT PRIMARY KEY,
                schema_nome       TEXT NOT NULL,
                iniciado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
                etapa             TEXT NOT NULL DEFAULT 'bootstrap',
                publicado_em      TIMESTAMPTZ,
                linhas_estabelecimento BIGINT,
                delta_pct         NUMERIC(6,3),
                observacao        TEXT
            );
            """
        )
    conn.commit()


def registrar_etapa(conn, load_id: str, etapa: str, **campos) -> None:
    """
    Marca em que ponto o ciclo esta.

    E o que torna `cycle` retomavel: cada etapa pergunta o estado antes de
    refazer o trabalho. Sem isso, uma queda na hora 5 significa recomecar da
    hora 0.
    """
    # A tabela e garantida aqui, e nao so no bootstrap: o ciclo pode ser
    # RETOMADO a partir de qualquer etapa — inclusive de um `publish` avulso —
    # e exigir que o bootstrap tenha rodado nesta sessao faria a retomada
    # falhar por um motivo que nao tem a ver com os dados.
    criar_tabela_de_ciclos(conn)

    colunas = ", ".join(f"{k} = %({k})s" for k in campos)
    parametros = {"load_id": load_id, "etapa": etapa, **campos}

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TABELA_CICLOS} (load_id, schema_nome, etapa)
            VALUES (%(load_id)s, %(schema_nome)s, %(etapa)s)
            ON CONFLICT (load_id) DO UPDATE SET etapa = EXCLUDED.etapa
            {(', ' + colunas) if colunas else ''};
            """,
            {**parametros, "schema_nome": st.nome_do_schema(load_id)},
        )
    conn.commit()


def etapa_atual(conn, load_id: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT etapa FROM {TABELA_CICLOS} WHERE load_id = %s;", (load_id,))
        linha = cur.fetchone()

    return linha[0] if linha else None


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------
def bootstrap(conn, load_id: str, database: str) -> str:
    """
    Prepara o ciclo: schemas de infraestrutura, schema novo e a salvaguarda.

    A ORDEM AQUI E A PARTE QUE IMPORTA. O ``REVOKE`` vem ANTES da criacao do
    schema novo, e nao depois: aplicado no fim, ele protegeria a proxima
    execucao e nao esta.
    """
    schema_novo = st.nome_do_schema(load_id)

    st.criar_schemas_de_infraestrutura(conn)
    criar_tabela_de_ciclos(conn)

    vigente = st.schema_vigente(conn, database)

    # A salvaguarda, primeiro.
    if vigente and vigente != schema_novo:
        st.revogar_escrita_no_schema_vigente(conn, vigente)
    elif vigente == schema_novo:
        # Reexecucao sobre o mesmo load_id: revogar a escrita do schema que
        # ESTA sendo construido tornaria o ciclo impossivel de retomar.
        print_log(
            f"o schema {schema_novo} ja e o vigente — reexecucao sobre a carga publicada",
            "WARNING",
        )

    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_novo}";')

    conn.commit()
    st.definir_schema_alvo(schema_novo)
    st.definir_search_path(conn, schema_novo)
    registrar_etapa(conn, load_id, "bootstrap")

    print_log(f"bootstrap concluido: escrevendo em {schema_novo}, vigente = {vigente}", "SUCCESS")

    return schema_novo


# ---------------------------------------------------------------------------
# o gate de delta
# ---------------------------------------------------------------------------
def gate_de_delta(conn, schema_novo: str, schema_vigente: Optional[str]) -> ResultadoDoGate:
    """
    Compara os dois schemas por ``row_hash`` e decide se a publicacao segue.

    A comparacao e um FULL OUTER JOIN sobre duas PKs ja ordenadas — merge join,
    sem sort. Com 72,32 M linhas de 22 bytes dos dois lados sao ~3,2 GB de
    leitura sequencial: ~10 segundos numa NVMe.

    Delta acima de ``MAX_DELTA_PCT`` ABORTA. Nao e conservadorismo: e a
    assinatura de arquivo-fonte truncado e de bug de parsing, e os dois produzem
    uma carga que parece completa. Sem o gate, ela seria publicada.
    """
    if not schema_vigente:
        # Primeira carga: nao ha com o que comparar, e exigir comparacao aqui
        # impediria a primeira publicacao para sempre.
        return ResultadoDoGate(0.0, 0, 0, 0, True, "primeira carga: sem geracao anterior")

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH comparacao AS (
                SELECT
                    n.cnpj_completo AS novo,
                    v.cnpj_completo AS vigente,
                    n.row_hash      AS hash_novo,
                    v.row_hash      AS hash_vigente
                FROM "{schema_novo}".busca_estabelecimento n
                FULL OUTER JOIN "{schema_vigente}".busca_estabelecimento v
                    ON n.cnpj_completo = v.cnpj_completo
            )
            SELECT
                count(*) FILTER (WHERE vigente IS NULL)                          AS novas,
                count(*) FILTER (WHERE novo IS NULL)                             AS removidas,
                count(*) FILTER (WHERE novo IS NOT NULL AND vigente IS NOT NULL
                                   AND hash_novo IS DISTINCT FROM hash_vigente)  AS alteradas,
                count(*) FILTER (WHERE vigente IS NOT NULL)                      AS total_vigente
            FROM comparacao;
            """
        )
        novas, removidas, alteradas, total_vigente = cur.fetchone()

    total_vigente = max(int(total_vigente or 0), 1)
    mudadas = int(novas) + int(removidas) + int(alteradas)
    delta = 100.0 * mudadas / total_vigente
    aprovado = delta <= MAX_DELTA_PCT

    detalhe = (
        f"novas={novas} removidas={removidas} alteradas={alteradas} "
        f"sobre {total_vigente} → delta={delta:.3f}% (teto {MAX_DELTA_PCT}%)"
    )

    print_log(f"gate de delta: {detalhe}", "SUCCESS" if aprovado else "ERROR")

    return ResultadoDoGate(delta, int(novas), int(removidas), int(alteradas), aprovado, detalhe)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def validate(
    conn,
    schema_novo: str,
    schema_vigente: Optional[str],
    *,
    minimo_estabelecimentos: int = 60_000_000,
) -> ResultadoDoGate:
    """
    O portao de qualidade de D0 04:30. Nada avanca se ele reprovar.

    Cada checagem cobre um modo de falha REAL da carga:

    * contagem minima  — arquivo-fonte truncado, que carrega sem erro;
    * MVs presentes    — o build falhou e ninguem viu, e o site perde as
                         estatisticas territoriais inteiras;
    * gate de delta    — bug de parsing, que produz linhas plausiveis e erradas.
    """
    falhas = []

    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{schema_novo}".estabelecimento;')
        total = int(cur.fetchone()[0])

        if total < minimo_estabelecimentos:
            falhas.append(f"estabelecimento tem {total} linhas, abaixo do minimo de {minimo_estabelecimentos}")

        cur.execute(
            """
            SELECT count(*) FROM pg_matviews WHERE schemaname = %s;
            """,
            (schema_novo,),
        )
        mvs = int(cur.fetchone()[0])

        if mvs == 0:
            falhas.append("nenhuma materialized view no schema novo")

        # MV vazia passa por qualquer contagem de MVs e quebra a pagina que a
        # consome — e o sintoma aparece como "o ranking sumiu", nao como erro.
        cur.execute(
            """
            SELECT matviewname FROM pg_matviews WHERE schemaname = %s LIMIT 20;
            """,
            (schema_novo,),
        )
        for (nome,) in cur.fetchall():
            cur.execute(f'SELECT EXISTS (SELECT 1 FROM "{schema_novo}"."{nome}" LIMIT 1);')

            if not cur.fetchone()[0]:
                falhas.append(f"materialized view {nome} esta VAZIA")

    resultado = gate_de_delta(conn, schema_novo, schema_vigente)

    if not resultado.aprovado:
        falhas.append(f"gate de delta reprovou: {resultado.detalhe}")

    if falhas:
        raise CicloAbortado(
            "portao de qualidade reprovou; NADA foi publicado:\n  - " + "\n  - ".join(falhas)
        )

    print_log(f"portao de qualidade: {total} estabelecimentos, {mvs} MVs, {resultado.detalhe}", "SUCCESS")

    return resultado


# ---------------------------------------------------------------------------
# publish e rollback
# ---------------------------------------------------------------------------
def publish(conn, load_id: str, database: str, *, pular_validacao: bool = False) -> None:
    """
    Publica o schema novo. Menos de 5 segundos.

    A validacao roda AQUI e nao antes: entre o fim da carga e o publish pode
    ter passado tempo, e um portao de qualidade que roda cedo demais aprova um
    estado que ja mudou.
    """
    schema_novo = st.nome_do_schema(load_id)
    vigente = st.schema_vigente(conn, database)

    if vigente == schema_novo:
        print_log(f"{schema_novo} ja e o schema publicado — no-op", "INFO")
        return

    if not pular_validacao:
        resultado = validate(conn, schema_novo, vigente)
        registrar_etapa(
            conn, load_id, "validado",
            delta_pct=round(resultado.delta_pct, 3),
        )

    st.publicar_schema(conn, schema_novo, database)

    # O schema que SAIU volta a ser gravavel: ele deixou de estar servindo, e
    # manter o REVOKE nele impediria o GC de apaga-lo mais tarde.
    if vigente:
        st.devolver_escrita_no_schema(conn, vigente)

    registrar_etapa(conn, load_id, "publicado", publicado_em="now()")

    print_log(
        f"publicado {schema_novo}. O anterior ({vigente}) FICA — o rollback e a "
        f"troca inversa, e o GC e do N-2, nunca do N-1.",
        "SUCCESS",
    )


def rollback(conn, database: str, *, para: Optional[str] = None) -> str:
    """
    Volta o ``search_path`` para a geracao anterior.

    E a mesma operacao do publish, na direcao contraria, e leva o mesmo tempo.
    Existe porque a alternativa — restaurar 134 GB de backup — leva ~40 minutos
    e perde tudo o que aconteceu depois.
    """
    vigente = st.schema_vigente(conn, database)

    if para is None:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT schema_nome FROM {TABELA_CICLOS}
                WHERE publicado_em IS NOT NULL AND schema_nome <> %s
                ORDER BY publicado_em DESC LIMIT 1;
                """,
                (vigente or "",),
            )
            linha = cur.fetchone()

        if not linha:
            raise CicloAbortado(
                "nao ha geracao anterior publicada para voltar. "
                "O rollback depende da retencao do N-1 — se ele foi apagado, o "
                "caminho e o restore do backup (~40 min de RTO)."
            )

        para = linha[0]

    st.publicar_schema(conn, para, database)
    print_log(f"rollback: {vigente} → {para}", "SUCCESS")

    return para


# ---------------------------------------------------------------------------
# GC — do N-2, nunca do N-1
# ---------------------------------------------------------------------------
def schemas_para_expurgo(conn, database: str) -> list:
    """
    Schemas de dados elegiveis a remocao.

    Devolve a LISTA, nunca apaga — a mesma separacao do expurgo de indices do
    motor de busca, e pelo mesmo motivo: o defeito original desta operacao
    nasceu de destruir dentro de um caminho automatico.

    O N-1 NUNCA entra na lista. Ele e o rollback.
    """
    vigente = st.schema_vigente(conn, database)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT schema_nome FROM {TABELA_CICLOS}
            WHERE publicado_em IS NOT NULL
            ORDER BY publicado_em DESC;
            """
        )
        publicados = [linha[0] for linha in cur.fetchall()]

    # Preserva o vigente e o imediatamente anterior.
    preservar = set(filter(None, [vigente, *publicados[:2]]))

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT nspname FROM pg_namespace
            WHERE nspname LIKE %s ORDER BY nspname;
            """,
            (st.PREFIXO_SCHEMA + "_%",),
        )
        todos = [linha[0] for linha in cur.fetchall()]

    return [s for s in todos if s not in preservar]
