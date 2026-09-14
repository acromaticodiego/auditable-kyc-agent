from fastapi import FastAPI

from app.api import health

app = FastAPI(
    title="Agente de verificacion de identidad (KYC)",
    description=(
        "Decide aprobar, rechazar, escalar a revision humana o solicitar un "
        "reenvio, razonando sobre senales de OCR, similitud facial y calidad "
        "de imagen.  Cada decision va acompanada de fundamentos que citan la "
        "senal concreta que los sostiene."
    ),
    version="0.1.0",
)

app.include_router(health.router)
