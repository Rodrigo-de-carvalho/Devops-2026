"""
Módulo compartilhado: conexão com o Postgres da Aiven e leitura da API
de dados abertos do Banco Central (SGS). Usado por app.py, producer.py
e consumer.py — evita duplicar a lógica de acesso a dados em cada script.
"""

import os
import datetime
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool as psycopg2_pool
from psycopg2.extras import RealDictCursor
import requests
import calendar

DB_URL = os.environ.get("AIVEN_DB_URL")

# O dashboard mostra DUAS fontes de dados lado a lado, no mesmo gráfico:
#
#   1. "original" — as séries do SGS/BCB do trabalho original (Dólar, Selic,
#      IPCA). Dados abertos, sem autenticação.
#      Referência completa de códigos: https://www3.bcb.gov.br/sgspub
#   2. "master" — os dados do IF.data (Banco Central) sobre o Banco Master,
#      conglomerado prudencial (trabalho novo).
#
# Os códigos das duas fontes não se repetem, então as duas convivem na
# mesma tabela bcb_series sem conflito.
SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
# Janela buscada no SGS: cobre os últimos POINTS_PER_SERIES meses.
SGS_JANELA_DIAS = 3 * 365

# Dados do IF.data (Banco Central) sobre o Banco Master — conglomerado prudencial.
# Consulta: https://olinda.bcb.gov.br/olinda/servico/IFDATA/versao/v1/aplicacao
IFDATA_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/IFDATA/versao/v1/odata/"
    "IfDataValores(AnoMes=@AnoMes,TipoInstituicao=@TipoInstituicao,Relatorio=@Relatorio)"
)
COD_INST = "C0080367"          # BANCO MASTER - PRUDENCIAL
PRIMEIRO_TRIMESTRE = (2020, 3)  # (ano, mês) do primeiro trimestre buscado

FONTES = {
    "original": "Trabalho original — SGS/BCB",
    "master": "Trabalho novo — Banco Master (IF.data)",
}

# Fonte "original": a chave é o código da série no SGS.
# Fonte "master": a chave é o número da "Conta" no IF.data (relatório Resumo);
# "escala" converte o Saldo para a unidade exibida (reais -> bilhões, fração -> %).
SERIES = {
    1: {"name": "Dólar comercial (venda)", "unit": "R$", "casas": 4, "fonte": "original"},
    432: {"name": "Meta Selic (Copom)", "unit": "% a.a.", "casas": 2, "fonte": "original"},
    433: {"name": "IPCA — variação mensal", "unit": "%", "casas": 2, "fonte": "original"},
    78182: {"name": "Ativo Total", "unit": "R$ bi", "casas": 2, "escala": 1e-9, "fonte": "master"},
    78186: {"name": "Patrimônio Líquido", "unit": "R$ bi", "casas": 2, "escala": 1e-9, "fonte": "master"},
    78187: {"name": "Lucro Líquido", "unit": "R$ bi", "casas": 2, "escala": 1e-9, "fonte": "master"},
    79664: {"name": "Índice de Basileia", "unit": "%", "casas": 2, "escala": 100, "fonte": "master"},
}
POINTS_PER_SERIES = 30

# Pool de conexões: a Aiven fica em outra região/rede que o Render, então
# abrir uma conexão nova (TCP + handshake SSL) custa ~1s toda vez. Uma
# única página do dashboard faz várias consultas (visita, cache de cada
# série, contagem de acessos...), e abrir uma conexão nova pra cada uma
# multiplicava esse ~1s por 7-8, deixando a página bem lenta. Com o pool,
# as conexões são abertas uma vez e reaproveitadas entre requisições.
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DB_URL:
            raise RuntimeError("AIVEN_DB_URL não configurada")
        _pool = psycopg2_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=DB_URL,
            sslmode="require",
            cursor_factory=RealDictCursor,
            connect_timeout=10,
        )
    return _pool


@contextmanager
def get_connection():
    """Pega uma conexão emprestada do pool e devolve ao sair do bloco
    `with` — em vez de abrir/fechar uma conexão física nova toda vez."""
    conn = _get_pool().getconn()
    try:
        yield conn
    except Exception:
        # Se algo deu errado no meio de uma transação, desfaz antes de
        # devolver ao pool — senão a próxima requisição que reaproveitar
        # essa conexão herda uma transação "abortada" e falha em cascata.
        conn.rollback()
        raise
    finally:
        _get_pool().putconn(conn)


def init_db():
    """Cria as tabelas caso ainda não existam.

    Em produção, vários workers do gunicorn chamam esta função quase ao
    mesmo tempo na inicialização. `CREATE TABLE IF NOT EXISTS` não é
    100% atômico entre transações concorrentes no Postgres, então dois
    workers podem colidir tentando criar a mesma tabela ao mesmo tempo
    (erro de "duplicate key" no catálogo interno pg_type). Isso é
    inofensivo — só significa que outro worker já criou a tabela — então
    apenas ignoramos esse erro específico em vez de deixar o worker
    inteiro cair.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bcb_series (
                        series_code INTEGER NOT NULL,
                        ref_date    DATE NOT NULL,
                        value       NUMERIC NOT NULL,
                        fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (series_code, ref_date)
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visits (
                        id SERIAL PRIMARY KEY,
                        path TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    """
                )
            conn.commit()
    except psycopg2.errors.DuplicateTable:
        # Outro worker criou a tabela entre nossa checagem e nosso CREATE.
        # Tudo bem, o objetivo (tabela existir) já foi alcançado.
        pass
    except psycopg2.errors.UniqueViolation as exc:
        # Mesma corrida, mas manifestada como conflito no catálogo interno
        # pg_type em vez de "table already exists". Também inofensivo.
        if "pg_type" not in str(exc):
            raise


_cache_trimestres = {}


def _trimestres():
    """Lista os fins de trimestre ('AAAAMM') do PRIMEIRO_TRIMESTRE até hoje."""
    hoje = datetime.date.today()
    ano, mes = PRIMEIRO_TRIMESTRE
    lista = []
    while (ano, mes) <= (hoje.year, hoje.month):
        lista.append(f"{ano}{mes:02d}")
        mes += 3
        if mes > 12:
            ano, mes = ano + 1, 3
    return lista


def _buscar_trimestre(ano_mes: str):
    """Busca o relatório Resumo do Banco Master em um trimestre. Guarda em
    memória para as 4 séries não repetirem a mesma chamada (trimestres sem
    dados não são guardados, para serem tentados de novo mais tarde)."""
    if ano_mes in _cache_trimestres:
        return _cache_trimestres[ano_mes]
    url = (
        f"{IFDATA_URL}?@AnoMes={ano_mes}&@TipoInstituicao=1&@Relatorio='1'"
        f"&$filter=CodInst%20eq%20'{COD_INST}'&$format=json"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    linhas = resp.json()["value"]
    if linhas:
        _cache_trimestres[ano_mes] = linhas
    return linhas


def fetch_series_from_bcb(series_code: int, n: int = POINTS_PER_SERIES):
    """Busca os últimos n pontos de uma série, na fonte certa (SGS para as
    séries do trabalho original, IF.data para as do Banco Master). Usado por
    app.py, producer.py e consumer.py."""
    if SERIES[series_code]["fonte"] == "original":
        return _fetch_sgs(series_code, n)
    return _fetch_ifdata(series_code, n)


def _fetch_sgs(series_code: int, n: int):
    """Série do SGS/BCB (código do trabalho original). Usa o endpoint /dados
    com dataInicial/dataFinal — o /ultimos/{n} passou a responder 400 — e
    recorta os últimos n pontos localmente.

    Séries diárias (Dólar, Selic) ficam com o último valor de cada mês: assim
    as séries do trabalho original cobrem o mesmo período que as do Banco
    Master e dá para comparar as duas no mesmo gráfico."""
    hoje = datetime.date.today()
    inicio = hoje - datetime.timedelta(days=SGS_JANELA_DIAS)
    resp = requests.get(
        SGS_URL.format(codigo=series_code),
        params={
            "formato": "json",
            "dataInicial": inicio.strftime("%d/%m/%Y"),
            "dataFinal": hoje.strftime("%d/%m/%Y"),
        },
        timeout=20,
    )
    resp.raise_for_status()
    por_mes = {}
    for item in resp.json():
        ref_date = datetime.datetime.strptime(item["data"], "%d/%m/%Y").date()
        por_mes[(ref_date.year, ref_date.month)] = {
            "ref_date": ref_date,
            "value": float(item["valor"]),
        }
    return [por_mes[mes] for mes in sorted(por_mes)][-n:]


def _fetch_ifdata(series_code: int, n: int):
    """Série do Banco Master (uma "Conta" do IF.data), trimestre a trimestre."""
    escala = SERIES[series_code]["escala"]
    parsed = []
    for ano_mes in _trimestres()[-n:]:
        for item in _buscar_trimestre(ano_mes):
            if item["Conta"] == str(series_code) and item["Saldo"] is not None:
                ano, mes = int(ano_mes[:4]), int(ano_mes[4:])
                ultimo_dia = calendar.monthrange(ano, mes)[1]
                parsed.append({
                    "ref_date": datetime.date(ano, mes, ultimo_dia),
                    "value": float(item["Saldo"]) * escala,
                })
    return parsed


def upsert_series(series_code: int, points: list):
    """Grava (ou atualiza) pontos de uma série direto no Postgres —
    usado tanto pelo cache do app.py quanto pelo consumer.py do Kafka."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            for p in points:
                cur.execute(
                    """
                    INSERT INTO bcb_series (series_code, ref_date, value, fetched_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (series_code, ref_date)
                    DO UPDATE SET value = EXCLUDED.value, fetched_at = now();
                    """,
                    (series_code, p["ref_date"], p["value"]),
                )
        conn.commit()
