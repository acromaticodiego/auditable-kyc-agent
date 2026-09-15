from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, verifications
from app.db import engine
from app.storage.models import ensure_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Las tablas se crean al arrancar porque no hay Alembic: es un monolito
    # de un solo desarrollador y una herramienta de migraciones seria mas
    # ceremonia que ayuda mientras el esquema cabe en un fichero.  La
    # limitacion esta anotada en app/storage/models.py y no es gratis: en
    # cuanto haya datos que no se puedan perder, esto necesita migraciones.
    ensure_schema(engine)
    yield


app = FastAPI(
    title="Agente de verificacion de identidad (KYC)",
    description=(
        "Decide aprobar, rechazar, escalar a revision humana o solicitar un "
        "reenvio, razonando sobre senales de OCR, similitud facial y calidad "
        "de imagen.  Cada decision va acompanada de fundamentos que citan la "
        "senal concreta que los sostiene."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(verifications.router)
