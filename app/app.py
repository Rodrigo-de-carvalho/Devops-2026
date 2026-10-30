"""
Dashboard de Indicadores Econômicos — App Flask
Lê os indicadores do Postgres da Aiven (tabela bcb_series). Mostra, no mesmo
gráfico, as séries do trabalho original (SGS/BCB: Dólar, Selic, IPCA) e as
do trabalho novo (IF.data: Banco Master). Os dados chegam
lá de duas formas possíveis, que podem conviver:

  1. Cache batch: este próprio app busca dados novos na API do BCB quando
     o cache está velho (ver refresh_stale_series abaixo) — funciona sozinho,
     sem depender de Kafka.
  2. Streaming: producer.py publica no Kafka, consumer.py assina o tópico
     e grava aqui — ver README para a arquitetura completa.

Variáveis de ambiente esperadas (ver .env.example):
  AIVEN_DB_URL   -> string de conexão fornecida pela Aiven
  PORT           -> porta HTTP (default 8080)
  CACHE_HOURS    -> horas antes de buscar dados novos na API (default 6)
"""

import os
import time
import datetime
import threading
from flask import Flask, render_template, jsonify
import requests

import bcb_client as bcb

app = Flask(__name__)

CACHE_HOURS = int(os.environ.get("CACHE_HOURS", 6))
# Intervalo mínimo entre duas tentativas de atualizar o cache — evita que
# cada visita dispare de novo a busca na API do BCB quando ela está falhando.
REFRESH_RETRY_SECONDS = int(os.environ.get("REFRESH_RETRY_SECONDS", 600))
START_TIME = time.time()

_refresh_lock = threading.Lock()
_last_refresh_attempt = 0.0

# Garante que as tabelas existam assim que o módulo é carregado — necessário
# porque em produção o app roda via gunicorn (`gunicorn app:app`), que nunca
# executa o bloco `if __name__ == "__main__":` lá embaixo.
bcb.init_db()


def log_visit(path: str):
    with bcb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO visits (path) VALUES (%s);", (path,))
        conn.commit()


def is_cache_stale(series_code: int) -> bool:
    with bcb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(fetched_at) AS last_fetch FROM bcb_series WHERE series_code = %s;",
                (series_code,),
            )
            row = cur.fetchone()
    if not row or not row["last_fetch"]:
        return True
    age = datetime.datetime.now(datetime.timezone.utc) - row["last_fetch"]
    return age > datetime.timedelta(hours=CACHE_HOURS)


def refresh_stale_series():
    """Cache batch: só busca na API do BCB se ninguém (nem o consumer do
    Kafka) já atualizou essa série recentemente."""
    for code in bcb.SERIES:
        if is_cache_stale(code):
            try:
                points = bcb.fetch_series_from_bcb(code)
                bcb.upsert_series(code, points)
            except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
                app.logger.warning("Falha ao atualizar série %s: %s", code, exc)


def refresh_in_background():
    """Dispara refresh_stale_series em uma thread separada, sem bloquear a
    requisição. A busca no IF.data percorre dezenas de trimestres e pode
    passar do timeout do gunicorn (60s) — se rodasse dentro da requisição,
    a página ficaria carregando até o worker ser morto."""
    global _last_refresh_attempt
    if time.time() - _last_refresh_attempt < REFRESH_RETRY_SECONDS:
        return
    if not _refresh_lock.acquire(blocking=False):
        return  # já tem uma atualização em andamento
    _last_refresh_attempt = time.time()

    def run():
        try:
            refresh_stale_series()
        except Exception:  # noqa: BLE001
            app.logger.exception("Falha ao atualizar o cache em segundo plano")
        finally:
            _refresh_lock.release()

    threading.Thread(target=run, daemon=True).start()


def get_series_data(series_code: int):
    """Os POINTS_PER_SERIES pontos mais recentes da série, em ordem cronológica."""
    with bcb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ref_date, value
                FROM bcb_series
                WHERE series_code = %s
                ORDER BY ref_date DESC
                LIMIT %s;
                """,
                (series_code, bcb.POINTS_PER_SERIES),
            )
            return list(reversed(cur.fetchall()))


def get_dashboard_data():
    refresh_in_background()

    indicators = []
    for code, meta in bcb.SERIES.items():
        rows = get_series_data(code)
        latest = rows[-1] if rows else None
        previous = rows[-2] if len(rows) > 1 else None
        delta = None
        if latest and previous:
            delta = round(float(latest["value"]) - float(previous["value"]), meta["casas"])

        indicators.append(
            {
                "code": code,
                "name": meta["name"],
                "unit": meta["unit"],
                "fonte": meta["fonte"],
                "fonte_label": bcb.FONTES[meta["fonte"]],
                "latest_value": round(float(latest["value"]), meta["casas"]) if latest else None,
                "latest_date": latest["ref_date"].strftime("%d/%m/%Y") if latest else None,
                "delta": delta,
                "labels": [r["ref_date"].strftime("%d/%m/%Y") for r in rows],
                "dates": [r["ref_date"].isoformat() for r in rows],
                "values": [round(float(r["value"]), meta["casas"]) for r in rows],
            }
        )

    with bcb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM visits;")
            total_visits = cur.fetchone()["total"]

    return {
        "indicators": indicators,
        "fontes": bcb.FONTES,
        "total_visits": total_visits,
        "uptime_seconds": int(time.time() - START_TIME),
        "server_time": datetime.datetime.utcnow().isoformat() + "Z",
        "cache_hours": CACHE_HOURS,
    }


@app.route("/")
def dashboard():
    log_visit("/")
    data = get_dashboard_data()
    return render_template("dashboard.html", data=data)


@app.route("/api/indicators")
def api_indicators():
    log_visit("/api/indicators")
    return jsonify(get_dashboard_data())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Força a atualização de todas as séries via cache batch, ignorando a
    idade do cache. Útil como fallback quando não se está usando Kafka."""
    for code in bcb.SERIES:
        try:
            points = bcb.fetch_series_from_bcb(code)
            bcb.upsert_series(code, points)
        except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
            return jsonify({"status": "error", "series": code, "detail": str(exc)}), 502
    return jsonify({"status": "ok", "refreshed": list(bcb.SERIES.keys())})


@app.route("/healthz")
def healthz():
    """Usado pelo pipeline de CI/CD e pela plataforma de deploy (health check)."""
    try:
        with bcb.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        return jsonify({"status": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "detail": str(exc)}), 503


if __name__ == "__main__":
    refresh_stale_series()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
