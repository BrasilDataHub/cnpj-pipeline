-- =============================================================================
-- Funções auxiliares das Materialized Views (âncora temporal e indicadores)
-- =============================================================================
-- Este arquivo roda ANTES de todas as MVs (prefixo "00") porque elas dependem
-- das funções aqui definidas. É idempotente (CREATE OR REPLACE) e não recria
-- nenhuma MV — pode ser reexecutado isoladamente com `--only 00`.
--
-- Tempo estimado de criação: instantâneo
-- Periodicidade de refresh recomendada: n/a (roda junto de qualquer build)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- fn_mes_ancora() - Último mês COMPLETO representado na carga
-- -----------------------------------------------------------------------------
-- Toda janela temporal das MVs é ancorada aqui, NUNCA em CURRENT_DATE: o build
-- de uma MV precisa ser reprodutível (rodar a mesma MV amanhã não pode mudar o
-- recorte) e o mês corrente da carga é sempre parcial — a RFB publica no início
-- do mês seguinte, então o último mês com dados é o mês anterior à publicação.
--
-- Dois braços independentes, combinados por LEAST (o mais conservador vence),
-- porque cada um corrige o modo de falha do outro:
--   1. pipeline_stats.reference_period ('YYYY-MM' da carga) − 1 mês.
--      Falha quando a tabela ainda não existe (banco novo) ou o período não foi
--      registrado; e mente quando as MVs são recriadas sem recarregar os dados.
--   2. date_trunc(max(data_inicio_atividade)) − 1 mês.
--      Falha quando a base tem datas futuras digitadas errado pela fonte (a RFB
--      tem registros com data_inicio_atividade em 2099); acerta sempre que a
--      carga é a fonte da verdade.
--
-- Escrita em plpgsql (e não em SQL puro) de propósito: o corpo de uma função
-- SQL é validado na criação, o que exigiria pipeline_stats existente para
-- CREATE FUNCTION. Em plpgsql a referência é resolvida em tempo de execução e
-- a ausência da tabela vira NULL (braço 1 descartado) em vez de erro de build.
--
-- STABLE: o resultado não muda dentro da mesma transação, então o planejador
-- avalia uma única vez por consulta (as MVs a usam via CROSS JOIN de uma linha).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_mes_ancora()
RETURNS DATE
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    ancora_carga DATE;
    ancora_dados DATE;
BEGIN
    BEGIN
        SELECT (to_date(s.reference_period, 'YYYY-MM') - INTERVAL '1 month')::DATE
          INTO ancora_carga
          FROM pipeline_stats s
         WHERE s.reference_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
         ORDER BY s.started_at DESC
         LIMIT 1;
    EXCEPTION WHEN undefined_table THEN
        ancora_carga := NULL;
    END;

    BEGIN
        SELECT (date_trunc('month', max(e.data_inicio_atividade))::DATE - INTERVAL '1 month')::DATE
          INTO ancora_dados
          FROM estabelecimento e;
    EXCEPTION WHEN undefined_table THEN
        ancora_dados := NULL;
    END;

    -- LEAST ignora NULL, então basta ele para escolher o braço sobrevivente.
    -- O COALESCE final só cobre o caso degenerado (base vazia E sem histórico
    -- de cargas): sem ele a âncora seria NULL e TODAS as MVs sairiam vazias
    -- silenciosamente. Nesse cenário não existem dados para recortar, então o
    -- mês anterior ao corrente é um valor tão bom quanto qualquer outro.
    RETURN COALESCE(
        LEAST(ancora_carga, ancora_dados),
        (date_trunc('month', CURRENT_DATE) - INTERVAL '1 month')::DATE
    );
END;
$$;

COMMENT ON FUNCTION fn_mes_ancora() IS
'Último mês completo da carga (primeiro dia do mês), âncora de todas as janelas
temporais das Materialized Views. Carga 2026-07 => âncora 2026-06-01.
Nunca use CURRENT_DATE nas MVs: use esta função.';

-- -----------------------------------------------------------------------------
-- fn_variacao_pct() - Variação percentual entre período atual e anterior
-- -----------------------------------------------------------------------------
-- Contrato (o site renderiza "—" ou "Novo" quando vem NULL):
--   anterior = 0 (ou NULL) => NULL. Não existe "crescimento infinito".
--
-- A saturação em ±999999,99 existe porque as colunas de destino são
-- numeric(8,2): um município com 1 abertura no período anterior e alguns
-- milhares no atual estouraria o tipo e derrubaria o build inteiro da MV.
-- Saturar preserva o sinal e a ordem de grandeza; nesse regime o número já é
-- ilegível para o leitor de qualquer forma.
--
-- IMMUTABLE + SQL puro: o planejador faz inline da expressão, sem custo de
-- chamada por linha.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_variacao_pct(atual BIGINT, anterior BIGINT)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN anterior IS NULL OR anterior = 0 THEN NULL
        ELSE LEAST(
                GREATEST(ROUND((atual - anterior) * 100.0 / anterior, 2), -999999.99),
                999999.99
             )
    END;
$$;

COMMENT ON FUNCTION fn_variacao_pct(BIGINT, BIGINT) IS
'Variação percentual (atual vs anterior) com 2 casas. NULL quando o período
anterior é zero. Satura em ±999999,99 para caber em numeric(8,2).';

-- -----------------------------------------------------------------------------
-- fn_sobrevivencia_pct() - Percentual da coorte que continua ativa
-- -----------------------------------------------------------------------------
-- NULL quando não houve aberturas no período: 0/0 não é 0%, é "não se aplica".
--
-- ATENÇÃO ao comparar sobrevivência entre períodos: a coorte atual é mais
-- jovem que a anterior e teve menos tempo para fechar, então a sobrevivência
-- do período atual é estruturalmente maior. O delta em pontos percentuais é
-- indicador de tendência, não medida de qualidade das empresas.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sobrevivencia_pct(ainda_ativos BIGINT, aberturas BIGINT)
RETURNS NUMERIC
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT ROUND(ainda_ativos * 100.0 / NULLIF(aberturas, 0), 2);
$$;

COMMENT ON FUNCTION fn_sobrevivencia_pct(BIGINT, BIGINT) IS
'Taxa de sobrevivência da coorte (ainda ativos / aberturas) com 2 casas.
NULL quando não houve aberturas no período.';
