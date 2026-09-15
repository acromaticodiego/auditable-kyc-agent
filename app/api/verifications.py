"""El endpoint que recibe las dos caras de la cedula y devuelve una decision.

Una nota sobre los codigos de estado, porque es donde es facil equivocarse:
si Gemini devuelve un 503, **esta peticion no responde 503**.  El proveedor
fallo, pero nuestro sistema si tomo una decision -- escalar a revision
humana -- y la tomo a proposito (ver docs/adr/0004).  Devolver 503 aqui le
diria al cliente que reintente, y reintentar una verificacion que ya esta
en la cola de un analista la duplicaria.

Los unicos errores de esta capa son los suyos: que falte un fichero o que
lo enviado no sea una imagen.
"""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.runner import run_agent
from app.config import settings
from app.db import engine
from app.storage import verifications as almacen

router = APIRouter(tags=["verificaciones"])


def get_agent_client() -> GeminiClient:
    """El cliente del modelo, como dependencia para poder sustituirlo.

    Los tests lo reemplazan por uno con transporte simulado.  No es una
    comodidad: sin esto, la suite gastaria el cupo diario de verdad y
    empezaria a fallar sola al llegar a veinte ejecuciones.
    """
    return GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        cache=ResponseCache(),
    )


def _abrir(fichero: UploadFile, contenido: bytes, campo: str) -> Image.Image:
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"el fichero enviado en '{campo}' no se pudo abrir como imagen "
                f"(nombre: {fichero.filename!r})"
            ),
        ) from error
    return imagen.convert("RGB")


@router.post(
    "/verificaciones",
    status_code=status.HTTP_201_CREATED,
    summary="Verifica una cedula y registra la decision",
)
async def crear_verificacion(
    anverso: UploadFile = File(..., description="Foto del anverso de la cedula"),
    reverso: UploadFile = File(..., description="Foto del reverso, con la MRZ"),
    modelo: GeminiClient = Depends(get_agent_client),
) -> dict:
    bytes_anverso = await anverso.read()
    bytes_reverso = await reverso.read()

    imagen_anverso = _abrir(anverso, bytes_anverso, "anverso")
    imagen_reverso = _abrir(reverso, bytes_reverso, "reverso")

    # Importado aqui y no arriba a proposito: `build_signals` arrastra OCR y
    # numpy, y ponerlo en la cabecera del modulo hace que el arranque de la
    # API cargue Tesseract aunque nadie vaya a verificar nada.
    from app.signals.pipeline import build_signals

    senales = build_signals(imagen_anverso, imagen_reverso)
    run = run_agent(senales, modelo)

    guardada = almacen.guardar(
        engine,
        run,
        anverso_sha256=almacen.hash_imagen(bytes_anverso),
        reverso_sha256=almacen.hash_imagen(bytes_reverso),
    )

    return {
        "id": str(guardada.id),
        "decision": guardada.decision,
        "resultado_del_agente": guardada.resultado,
        "resumen": run.decision.summary if run.decision is not None else None,
        "explicacion_fiel": run.faithful,
        "fundamentos": [
            {
                "signal_id": fundamento.signal_id,
                "valor_citado": fundamento.cited_value,
                "peso": fundamento.weight.value,
                "texto": fundamento.text,
                "auditoria": resultado.status.value,
            }
            for fundamento, resultado in zip(
                run.decision.groundings,
                run.audit.results if run.audit is not None else [],
                strict=True,
            )
        ]
        if run.decision is not None
        else [],
        "error": run.error,
    }


@router.get(
    "/verificaciones/{verificacion_id}",
    summary="El registro completo de una verificacion",
)
def leer_verificacion(verificacion_id: uuid.UUID) -> dict:
    """Devuelve tambien las senales medidas, no solo la decision.

    Es lo que hace auditable el registro: sin los valores que el agente
    tenia delante, sus fundamentos son afirmaciones que hay que creerse.
    """
    registro = almacen.obtener(engine, verificacion_id)
    if registro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no hay ninguna verificacion con id {verificacion_id}",
        )
    return registro
