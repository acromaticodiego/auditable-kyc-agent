"""Recorrido de cuatro actos por lo que el sistema hace.

Pensado para enseñarlo en una pantalla compartida, no para medir nada: los
numeros medidos estan en el README y salen de los scripts de evaluacion.

TRES DE LOS CUATRO ACTOS NO TOCAN LA API, Y ES DELIBERADO
----------------------------------------------------------

Una demo que depende de una llamada en vivo se puede caer delante de quien
la esta viendo.  No es hipotetico: escribiendo esto, tres modelos seguidos
devolvieron 503 y corte por tiempo en la misma tarde.

Los dos actos que de verdad convencen -- que el sistema caza una
manipulacion y que caza una suplantacion -- son deterministas y corren sin
red.  La decision del agente es el cuarto, y si no hay cupo el recorrido lo
dice y sigue, en vez de morirse a mitad.

El cuarto acto tiende a salir gratis con el uso: una respuesta buena queda
en cache y a partir de ahi se repite sin gastar ni esperar, que es ademas lo
que conviene delante de alguien.  Lo que no se cachea son los fallos, asi
que ensayar la demo un dia malo va gastando cupo; para eso esta `--sin-api`.

    docker compose exec api python scripts/demo.py
    docker compose exec api python scripts/demo.py --sin-api
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.runner import RunOutcome, run_agent
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.config import settings
from app.domain.completeness import adverse_signals
from app.evaluation.catalog import TODAY, person
from app.signals.face import FaceReader
from app.signals.pipeline import build_signals, contradictions
from app.synthetic.cedula import render_back, render_front
from app.synthetic.tampering import retouch_front

CARAS = Path("data/real/caras")
ANCHO = 74


def titulo(numero: int, texto: str) -> None:
    print()
    print("=" * ANCHO)
    print(f"ACTO {numero}. {texto}")
    print("=" * ANCHO)


def linea(etiqueta: str, valor) -> None:
    print(f"   {etiqueta:34} {valor}")


def retrato_y_selfie() -> tuple[Image.Image, Image.Image] | None:
    """Las dos fotos de la unica persona que aporto un par genuino."""
    carpeta = CARAS / "persona-00"
    if not carpeta.exists():
        return None
    documento = next(carpeta.glob("documento.*"), None)
    selfie = next(carpeta.glob("selfie.*"), None)
    if documento is None or selfie is None:
        return None
    return Image.open(documento), Image.open(selfie)


def otra_cara() -> Image.Image | None:
    for carpeta in sorted(CARAS.glob("persona-*")):
        if carpeta.name == "persona-00":
            continue
        foto = next(iter(sorted(carpeta.iterdir())), None)
        if foto is not None:
            return Image.open(foto)
    return None


def acto_uno(senales) -> None:
    titulo(1, "Una cedula legitima: de dos fotos a 28 senales medidas")
    print()
    print("   El agente no ve imagenes. Ve senales con su identificador, su")
    print("   valor y una descripcion, y tiene que citarlas para justificarse.")
    print()
    for identificador in (
        "quality.front_sharpness",
        "mrz.readable",
        "mrz.checks_ok",
        "ocr.surnames",
        "ocr.surnames_confidence",
        "cross.surnames",
        "cross.nuip",
        "document.age_years",
        "document.expired",
    ):
        senal = senales.get(identificador)
        if senal is None:
            continue
        valor = senal.value if senal.available else "NO DISPONIBLE"
        linea(identificador, valor)
    print()
    linea("senales en total", len(senales))
    linea("senales adversas", adverse_signals(senales) or "ninguna")


def acto_dos() -> None:
    titulo(2, "Manipulacion: una letra cambiada en el apellido")
    print()
    print("   El mismo dato viaja dos veces en la cedula: impreso en el")
    print("   anverso y codificado dentro de la MRZ del reverso. Se retoca")
    print("   solo el anverso, WALTEROS -> WALTEROZ, y se deja la MRZ como")
    print("   estaba. Es lo que haria una manipulacion torpe.")
    print()
    datos = person()
    retocados, mrz_original = retouch_front(datos, surnames="WALTEROZ")
    senales = build_signals(
        render_front(retocados), render_back(datos, mrz_lines=mrz_original),
        today=TODAY,
    )

    linea("apellido impreso en el anverso", senales.get("ocr.surnames").value)
    linea("cotejo anverso contra MRZ", senales.get("cross.surnames").value)
    linea("digitos de control de la MRZ", senales.get("mrz.checks_ok").value)
    print()
    print("   Los digitos de control CUADRAN: la MRZ es coherente consigo")
    print("   misma porque no se toco. Lo que delata la manipulacion es que")
    print("   las dos copias del mismo dato no coinciden.")
    print()
    linea("campos que se contradicen", contradictions(senales))
    linea("senales adversas", adverse_signals(senales))


def acto_tres(lector: FaceReader) -> None:
    titulo(3, "Suplantacion: documento impecable, cara de otra persona")
    fotos = retrato_y_selfie()
    impostor = otra_cara()
    if fotos is None or impostor is None:
        print()
        print("   (omitido: hacen falta fotos reales en data/real/caras/;")
        print("    ver docs/caras-para-la-senal-facial.md)")
        return

    retrato, selfie = fotos
    datos = person()
    anverso = render_front(datos, portrait=retrato)
    reverso = render_back(datos)

    print()
    print("   El documento es autentico y no tiene un solo defecto. Lo unico")
    print("   que cambia entre los dos casos es quien manda la selfie.")
    print()
    for etiqueta, cara in (("su titular", selfie), ("otra persona", impostor)):
        senales = build_signals(
            anverso, reverso, today=TODAY, selfie=cara, face_reader=lector
        )
        senal = senales.get("facial.similarity")
        valor = f"{senal.value:+.4f}" if senal.available else "NO DISPONIBLE"
        linea(f"selfie de {etiqueta}", f"facial.similarity = {valor}")
        linea("  digitos de control", senales.get("mrz.checks_ok").value)
        linea("  cotejos que fallan", contradictions(senales) or "ninguno")
        print()

    print("   Ninguna otra senal del sistema ve la diferencia: ni la MRZ, ni")
    print("   el OCR, ni la coherencia de fechas. Solo la cara. Es el unico")
    print("   fraude del proyecto que necesita la senal facial para existir.")


def acto_cuatro(senales, *, sin_api: bool) -> None:
    titulo(4, "La decision del agente, y la auditoria de lo que dice")
    modelo = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        cache=ResponseCache(),
        max_retries=0,
    )
    en_cache = modelo.is_cached(build_prompt(senales), DECISION_RESPONSE_SCHEMA)
    restantes = modelo.budget.remaining(settings.gemini_model)

    print()
    linea("modelo", settings.gemini_model)
    linea("respuesta en cache", "si (no gasta cupo)" if en_cache else "no")
    linea("cupo restante hoy", restantes)

    if not en_cache and (sin_api or not restantes):
        print()
        motivo = "por --sin-api" if sin_api else "sin cupo"
        print(f"   No hay respuesta cacheada y no se va a pedir ({motivo}), asi")
        print("   que este acto se queda sin decision. El recorrido sigue en")
        print("   pie: los tres actos que convencen no dependen de la API, y")
        print("   esa fue la razon de construirlos asi.")
        return

    run = run_agent(senales, modelo)
    print()
    linea("como acabo la vuelta", run.outcome.value)
    linea("decision del sistema", run.effective_decision.value)

    if run.outcome is not RunOutcome.DECIDED:
        # El cuerpo de un 503 de Gemini son ocho lineas de JSON. En una
        # pantalla compartida eso tapa la frase que importa, asi que se
        # aplasta a una linea.
        motivo = " ".join((run.error or "").split())
        print()
        print(f"   {motivo[:150]}")
        print()
        print("   El proveedor fallo y el sistema decidio igualmente: escalar a")
        print("   revision humana. Ni aprueba sin mirar ni acusa a nadie por")
        print("   una caida ajena. Ver docs/adr/0004.")
        return

    print()
    print(f"   {run.decision.summary}")
    print()
    print("   Cada fundamento cita una senal, y cada cita se contrasta contra")
    print("   el valor que esa senal tenia de verdad:")
    print()
    for fundamento, resultado in zip(
        run.decision.groundings, run.audit.results, strict=True
    ):
        marca = "ok" if resultado.valid else resultado.status.value
        print(
            f"   [{marca:>16}] {fundamento.signal_id} = "
            f"{fundamento.cited_value}  ({fundamento.weight.value})"
        )
    print()
    linea("explicacion fiel", run.faithful)
    linea("explicacion completa", run.complete)
    if run.completeness and run.completeness.omitted:
        linea("senales adversas que callo", run.completeness.omitted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sin-api",
        action="store_true",
        help="no pide nada al modelo. Util para ensayar la demo sin gastar cupo.",
    )
    args = parser.parse_args()

    print()
    print("Agente de verificacion de identidad (KYC)")
    print("Cuatro actos. Los tres primeros no tocan la API.")

    lector = FaceReader()
    datos = person()
    senales_limpias = build_signals(
        render_front(datos), render_back(datos), today=TODAY
    )

    acto_uno(senales_limpias)
    acto_dos()
    acto_tres(lector)
    acto_cuatro(senales_limpias, sin_api=args.sin_api)

    print()
    print("=" * ANCHO)
    print("Los numeros medidos, con su tamano de muestra y su procedencia,")
    print("estan en el README. Esto no mide nada: solo ensena que hace.")
    print("=" * ANCHO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
