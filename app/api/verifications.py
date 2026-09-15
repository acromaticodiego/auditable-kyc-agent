"""El endpoint que recibe las dos caras de la cedula y devuelve una decision.

Una nota sobre los codigos de estado, porque es donde es facil equivocarse:
si Gemini devuelve un 503, **esta peticion no responde 503**.  El proveedor
fallo, pero nuestro sistema si tomo una decision -- escalar a revision
humana -- y la tomo a proposito (ver docs/adr/0004).  Devolver 503 aqui le
diria al cliente que reintente, y reintentar una verificacion que ya esta
en la cola de un analista la duplicaria.

Los unicos errores de esta capa son los del cliente: que falte un fichero,
que lo enviado no sea una imagen, o que sea tan grande que procesarla sea
el ataque.
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

# Tope por fichero.  Una foto de cedula de un movil actual pesa entre 1 y 5
# MB; 10 deja margen de sobra para una captura generosa y corta la subida de
# 2 GB que dejaria al proceso sin memoria antes de llegar al OCR.
#
# Se comprueba leyendo a trozos y no con Content-Length: la cabecera la
# escribe quien sube el fichero y puede mentir.
MAX_BYTES_POR_FICHERO = 10 * 1024 * 1024

# Tope de pixeles.  PIL trae uno propio (89 millones) pero por debajo del
# doble solo AVISA, asi que una imagen de 100 megapixeles pasa entera y
# acaba en Tesseract, que es donde duele.  Una cedula fotografiada de cerca
# con un movil de 48 MP no llega a 50 millones; 40 es holgado para un
# documento y deja fuera lo que solo puede ser un ataque o un error.
MAX_PIXELES = 40_000_000


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


async def _leer_acotado(fichero: UploadFile, campo: str) -> bytes:
    """Lee el fichero sin pasar del tope, en vez de leerlo y medirlo despues.

    Leerlo entero y comprobar el tamano al final no defiende de nada: para
    cuando se sabe que son 2 GB, los 2 GB ya estan en memoria.
    """
    trozos: list[bytes] = []
    total = 0
    while trozo := await fichero.read(64 * 1024):
        total += len(trozo)
        if total > MAX_BYTES_POR_FICHERO:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"el fichero enviado en '{campo}' supera el maximo de "
                    f"{MAX_BYTES_POR_FICHERO // (1024 * 1024)} MB"
                ),
            )
        trozos.append(trozo)
    return b"".join(trozos)


def _abrir(fichero: UploadFile, contenido: bytes, campo: str) -> Image.Image:
    def rechazar(motivo: str, error: Exception) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"el fichero enviado en '{campo}' {motivo} "
                f"(nombre: {fichero.filename!r})"
            ),
        )

    try:
        imagen = Image.open(io.BytesIO(contenido))
    except (UnidentifiedImageError, OSError) as error:
        raise rechazar("no se pudo abrir como imagen", error) from error

    # El tamano se mira ANTES de `load()`, que es lo unico que sirve: la
    # cabecera de un PNG declara sus dimensiones en unos pocos bytes, y una
    # imagen bomba son justamente pocos bytes comprimidos que se expanden a
    # gigabytes al descomprimirlos. Comprobarlo despues de cargarla seria
    # comprobarlo cuando ya no hay nada que salvar.
    anchura, altura = imagen.size
    if anchura * altura > MAX_PIXELES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"la imagen enviada en '{campo}' tiene {anchura}x{altura} "
                f"pixeles y el maximo son {MAX_PIXELES:,}"
            ),
        )

    try:
        imagen.load()
    except Image.DecompressionBombError as error:
        # No hereda de OSError, asi que sin esta rama subiria como un 500.
        raise rechazar("es una imagen desproporcionada", error) from error
    except OSError as error:
        raise rechazar("esta truncado o corrupto", error) from error

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
    bytes_anverso = await _leer_acotado(anverso, "anverso")
    bytes_reverso = await _leer_acotado(reverso, "reverso")

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
