"""
Helper de conexão com o Aiven for Apache Kafka via SASL_SSL.

Variáveis de ambiente esperadas:
  KAFKA_BOOTSTRAP_SERVERS -> host:port (saída "kafka_bootstrap_servers" do Terraform)
  KAFKA_USERNAME           -> usuário SASL (saída "kafka_username")
  KAFKA_PASSWORD           -> senha SASL (saída "kafka_password")
  KAFKA_CA_CERT_PEM        -> conteúdo do certificado CA (saída "kafka_ca_cert")
  KAFKA_TOPIC               -> nome do tópico (default: bcb-indicadores)
"""

import os
import tempfile
from kafka import KafkaProducer, KafkaConsumer

TOPIC = os.environ.get("KAFKA_TOPIC", "bcb-indicadores")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "bcb-consumer")


def _ca_cert_path() -> str:
    """Grava o certificado CA (vindo de uma variável de ambiente/secret) em
    um arquivo temporário, pois as libs de Kafka esperam um caminho de arquivo."""
    pem = os.environ.get("KAFKA_CA_CERT_PEM")
    if not pem:
        raise RuntimeError("KAFKA_CA_CERT_PEM não configurada")
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(pem)
    return path


def _common_config() -> dict:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
    username = os.environ.get("KAFKA_USERNAME")
    password = os.environ.get("KAFKA_PASSWORD")
    if not all([bootstrap, username, password]):
        raise RuntimeError(
            "KAFKA_BOOTSTRAP_SERVERS, KAFKA_USERNAME e KAFKA_PASSWORD são obrigatórias"
        )
    return {
        "bootstrap_servers": bootstrap,
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_plain_username": username,
        "sasl_plain_password": password,
        "ssl_cafile": _ca_cert_path(),
    }


def get_producer() -> KafkaProducer:
    cfg = _common_config()
    return KafkaProducer(
        value_serializer=lambda v: v.encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k is not None else None,
        **cfg,
    )


def get_consumer() -> KafkaConsumer:
    cfg = _common_config()
    return KafkaConsumer(
        TOPIC,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: v.decode("utf-8"),
        key_deserializer=lambda k: k.decode("utf-8") if k is not None else None,
        **cfg,
    )
