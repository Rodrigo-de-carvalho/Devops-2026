"""
Producer — busca o ponto mais recente de cada série do BCB e publica no
tópico Kafka. Roda como job agendado (ver .github/workflows/kafka-produce.yml)
ou manualmente: `python producer.py`.

Não escreve no Postgres — essa é a responsabilidade do consumer.py, que
assina o mesmo tópico. O producer só materializa o evento "novo dado
disponível" no Kafka.
"""

import json
import sys

import bcb_client as bcb
import kafka_common


def main():
    producer = kafka_common.get_producer()
    published = 0

    for series_code in bcb.SERIES:
        try:
            # Só o ponto mais recente — o histórico completo já foi
            # semeado pelo cache batch do app.py na primeira execução.
            points = bcb.fetch_series_from_bcb(series_code, n=1)
        except Exception as exc:  # noqa: BLE001
            print(f"[producer] falha ao buscar série {series_code}: {exc}", file=sys.stderr)
            continue

        for p in points:
            event = {
                "series_code": series_code,
                "ref_date": p["ref_date"].isoformat(),
                "value": p["value"],
            }
            producer.send(
                kafka_common.TOPIC,
                key=str(series_code),
                value=json.dumps(event),
            )
            published += 1
            print(f"[producer] publicado: {event}")

    producer.flush(timeout=10)
    producer.close()
    print(f"[producer] concluído — {published} evento(s) publicado(s)")


if __name__ == "__main__":
    main()
