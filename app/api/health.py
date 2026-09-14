from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.db import engine

router = APIRouter(tags=["salud"])


def _check_database() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - la sonda nunca debe propagar
        return {"ok": False, "error": type(error).__name__}
    return {"ok": True}


@router.get("/health")
def health(response: Response) -> dict:
    """Sonda de salud.

    Devuelve 503 con el detalle de que dependencia falla, en vez de 200 a
    secas o de quedarse colgada esperando a la base de datos.  El timeout de
    conexion vive en la cadena DATABASE_URL (connect_timeout), porque un
    `SELECT 1` contra un host inalcanzable se queda esperando el TCP, no la
    consulta.
    """
    checks = {"database": _check_database()}
    healthy = all(check["ok"] for check in checks.values())

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if healthy else "degraded", "checks": checks}
