"""Mide si el agente decide bien cuando la cara no coincide.

Se separa de `evaluate_agent.py` porque mide otra cosa sobre otro material:
aquel usa el catalogo sintetico y publicable y mide verificacion de
**documento**; este usa fotos reales que no se publican y mide verificacion
de **identidad**.  Mezclarlos daria un solo numero sobre dos conjuntos
distintos, que es la clase de cifra que este proyecto no produce.

Lo que de verdad se pone a prueba aqui no es el reconocedor -- eso ya esta
medido y sale en el README -- sino si el agente **sabe usar un numero sin
umbral**.  La senal llega cruda y su descripcion le da las dos referencias
medidas; si aun asi aprueba una suplantacion, la decision de dejar el
umbral fuera del pipeline (ADR-0005) no funciona.

    docker compose exec api python scripts/evaluate_facial.py --simulacro
    docker compose exec api python scripts/evaluate_facial.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.runner import RunOutcome, run_agent
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.config import settings
from app.evaluation.baseline import decide as decide_baseline
from app.evaluation.facial_cases import build_facial_cases
from app.evaluation.rehearsal import MODELO_DE_ENSAYO, ModeloDeEnsayo
from app.signals.face import FaceReader
from app.signals.pipeline import build_signals

ANCHO = 78


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", default=settings.gemini_model)
    parser.add_argument(
        "--gastar",
        action="store_true",
        help=(
            "autoriza las peticiones. Sin esto calcula el plan, lo imprime "
            "y para sin tocar la API."
        ),
    )
    parser.add_argument(
        "--simulacro",
        action="store_true",
        help="ensaya sin llamar a la API. No mide nada del agente.",
    )
    args = parser.parse_args()

    casos = build_facial_cases()
    if not casos:
        print(
            "No hay casos que construir: hacen falta al menos dos identidades\n"
            "con fotos en data/real/caras/. Ver docs/caras-para-la-senal-facial.md"
        )
        return 1

    legitimos = [c for c in casos if not c.is_fraud]
    fraudes = [c for c in casos if c.is_fraud]
    print(
        f"{len(casos)} casos: {len(legitimos)} legitimos y "
        f"{len(fraudes)} de suplantacion"
    )
    if len(legitimos) < 2:
        print(
            f"\n  AVISO: solo hay {len(legitimos)} caso legitimo, porque solo una\n"
            "  persona aporto dos fotos suyas. Acertarlo o fallarlo no dice casi\n"
            "  nada; lo que este conjunto mide con alguna solidez es el lado de\n"
            "  la suplantacion."
        )

    print("\nMidiendo senales (OCR y caras, sin tocar la API)...")
    lector = FaceReader()
    senales = {
        caso.id: build_signals(
            caso.front, caso.back, selfie=caso.selfie, face_reader=lector
        )
        for caso in casos
    }

    nombre = MODELO_DE_ENSAYO if args.simulacro else args.modelo
    if args.simulacro:
        modelo = ModeloDeEnsayo(senales)
    else:
        modelo = GeminiClient(
            api_key=settings.gemini_api_key,
            model=args.modelo,
            cache=ResponseCache(),
            max_retries=0,
        )

    faltantes = [
        caso
        for caso in casos
        if not modelo.is_cached(
            build_prompt(senales[caso.id]), DECISION_RESPONSE_SCHEMA
        )
    ]
    disponibles = modelo.budget.remaining(nombre)

    print(f"\n{'=' * ANCHO}")
    print(f"PLAN (modelo {nombre})")
    print("=" * ANCHO)
    print(f"  casos                 {len(casos)}")
    print(f"  ya en cache           {len(casos) - len(faltantes)}")
    print(f"  peticiones necesarias {len(faltantes)}")
    print(
        "  cupo restante hoy     "
        + ("sin tope" if disponibles is None else str(disponibles))
    )

    if disponibles is not None and len(faltantes) > disponibles:
        print(
            f"\n  No alcanza: hacen falta {len(faltantes)} y quedan {disponibles}."
        )
        return 1

    # La misma puerta que en evaluate_agent.py, por el mismo motivo: ver
    # el plan no puede costar la tanda. Cerrarla solo alli dejaria abierta
    # la otra mitad, y las dos herramientas gastan del mismo cupo diario.
    if faltantes and not args.simulacro and not args.gastar:
        print()
        print(f"  PARADO: esto gastaria hasta {len(faltantes)} peticiones y no se")
        print("  ha autorizado. El plan de arriba ya esta calculado.")
        print()
        print("  Faltan por pedir: " + ", ".join(c.id for c in faltantes[:12]))
        print()
        print("  Para lanzarla de verdad, repetir anadiendo --gastar.")
        return 0

    aciertos = 0
    contestados = 0
    fieles = 0
    finales: Counter[str] = Counter()
    detalle: list[tuple[str, str, str, float | None, str]] = []

    print()
    print("caso".ljust(34) + "esperado".ljust(22) + "agente".ljust(22) + "similitud")
    print("-" * ANCHO)

    for caso in casos:
        conjunto = senales[caso.id]
        senal = conjunto.get("facial.similarity")
        valor = senal.value if senal and senal.available else None

        run = run_agent(conjunto, modelo)
        finales[run.outcome.value] += 1

        if run.outcome is RunOutcome.OUT_OF_QUOTA:
            print(f"\nSe acabo el cupo en {caso.id}. Se para la tanda.")
            break
        if run.outcome is RunOutcome.UNAVAILABLE:
            print(caso.id.ljust(34) + "(el proveedor no respondio)")
            continue

        contestados += 1
        ok = run.effective_decision is caso.expected_decision
        aciertos += ok
        fieles += run.faithful

        print(
            caso.id.ljust(34)
            + caso.expected_decision.value.ljust(22)
            + run.effective_decision.value.ljust(22)
            + (f"{valor:+.4f}" if valor is not None else "n/d").ljust(10)
            + ("ok" if ok else "XX")
        )
        detalle.append(
            (
                caso.id,
                caso.expected_decision.value,
                run.effective_decision.value,
                valor,
                run.decision.summary if run.decision else (run.error or ""),
            )
        )

    print(f"\n{'=' * ANCHO}")
    print(f"RESULTADO (modelo {nombre})")
    print("=" * ANCHO)
    if contestados == 0:
        print("  No se midio ningun caso.")
        return 1

    print(f"  agente               {aciertos}/{contestados} aciertos")
    print(f"  explicaciones fieles {fieles}/{contestados}")

    # La linea base no mira la cara, asi que aprueba toda suplantacion con un
    # documento impecable. No es un espantapajaros: es el resultado de no
    # tener la senal, y es exactamente lo que justifica anadirla.
    base_aciertos = sum(
        decide_baseline(senales[c.id]).decision is c.expected_decision
        for c in casos
    )
    print(
        f"  linea base           {base_aciertos}/{len(casos)} "
        "(no mira la cara: aprueba toda suplantacion)"
    )

    print("\n  Caso por caso:")
    for caso_id, esperado, obtenido, valor, dijo in detalle:
        marca = "ok" if esperado == obtenido else "XX"
        similitud = f"{valor:+.4f}" if valor is not None else "n/d"
        print(f"\n    [{marca}] {caso_id}  (similitud {similitud})")
        print(f"         esperado {esperado}, decidio {obtenido}")
        print(f"         {dijo[:220]}")

    print(
        f"\n  Muestra: {len(casos)} casos construidos con fotos reales que no se\n"
        "  publican, sobre una sola identidad de documento. El lado legitimo\n"
        "  depende de una sola persona; el de suplantacion, de tres."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
