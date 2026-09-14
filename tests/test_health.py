import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api import health
from app.main import app

client = TestClient(app)


def test_health_devuelve_503_cuando_la_base_de_datos_no_responde(monkeypatch):
    """El caso que de verdad importa: la sonda distingue caida de sana.

    Se sustituye `engine.connect`, no `_check_database`, para que el test
    recorra el try/except real y la traduccion a 503.  Sustituyendo la
    funcion entera el test pasaria en verde sin cubrir ese camino.
    """

    def connect_falla(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("conexion rechazada"))

    monkeypatch.setattr(health.engine, "connect", connect_falla)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False
    assert body["checks"]["database"]["error"] == "OperationalError"


@pytest.mark.integration
def test_health_devuelve_200_contra_la_base_de_datos_real():
    """Requiere el stack levantado; se ejecuta dentro del contenedor."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": {"ok": True}}}
