"""Evalua al agente sobre el conjunto de casos y lo compara con la linea base.

Por defecto solo mira **calibracion**, que es donde se puede iterar sobre el
prompt.  El reservado se pide con `--final` y esa ejecucion lo quema: a
partir de ahi, tocar el prompt mirando ese resultado convertiria la
siguiente medicion en otro numero elegido sobre sus propios datos.

El script cuenta lo que va a costar ANTES de gastar nada.  Cada caso que ya
esta en cache sale gratis; los que no, valen una peticion de las 20 del
dia.  Si no alcanza, no empieza: dejar una tanda a medias no da una medida
parcial, da doce casos de los que solo tres se midieron y nueve que
escalaron por falta de cupo, que es un numero sin sentido que ademas invita
a leerse como si lo tuviera.

    docker compose exec api python scripts/evaluate_agent.py
    docker compose exec api python scripts/evaluate_agent.py --final
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
from app.domain.citation_audit import audit_citations
from app.domain.signals import SignalSet
from app.evaluation.baseline import decide as decide_baseline
from app.evaluation.catalog import Case, TODAY, load_cases
from app.evaluation.split import CALIBRATION, HOLDOUT
from app.signals.pipeline import build_signals

ANCHO = 78


def preparar(casos: list[Case]) -> dict[str, SignalSet]:
    """Calcula las senales de cada caso.  Es lento pero no gasta cupo."""
    print(f"Midiendo senales de {len(casos)} casos (OCR, sin tocar la API)...")
    senales = {}
    for caso in casos:
        senales[caso.id] = build_signals(*caso.build(), today=TODAY)
    return senales


def contar_coste(
    casos: list[Case], senales: dict[str, SignalSet], modelo: GeminiClient
) -> list[Case]:
    """Los casos que exigiran una peticion de verdad."""
    return [
        caso
        for caso in casos
        if not modelo.is_cached(build_prompt(senales[caso.id]), DECISION_RESPONSE_SCHEMA)
    ]


def evaluar(
    casos: list[Case], senales: dict[str, SignalSet], modelo: GeminiClient
) -> dict:
    aciertos = 0
    fieles = 0
    citas_totales = 0
    citas_validas = 0
    finales: Counter[str] = Counter()
    desacuerdos: list[tuple[str, str, str, str, str]] = []
    base_aciertos = 0
    interrumpida = False

    print(f"\n{'caso':38} {'esperado':22} {'agente':22} {'base':10}")
    print("-" * ANCHO)

    for caso in casos:
        conjunto = senales[caso.id]
        base = decide_baseline(conjunto)
        base_ok = base.decision is caso.expected_decision
        base_aciertos += base_ok

        run = run_agent(conjunto, modelo)
        finales[run.outcome.value] += 1

        if run.outcome is RunOutcome.OUT_OF_QUOTA:
            print(f"\nSe acabo el cupo en {caso.id}. Se para la tanda.")
            print(f"  {run.error}")
            interrumpida = True
            break

        agente_ok = run.effective_decision is caso.expected_decision
        aciertos += agente_ok
        fieles += run.faithful
        if run.audit is not None:
            citas_totales += len(run.audit.results)
            citas_validas += sum(1 for r in run.audit.results if r.valid)

        marca = "ok" if agente_ok else "XX"
        print(
            f"{caso.id:38} {caso.expected_decision.value:22} "
            f"{run.effective_decision.value:22} {'ok' if base_ok else 'XX':4} {marca}"
        )

        if not agente_ok:
            desacuerdos.append(
                (
                    caso.id,
                    caso.expected_decision.value,
                    run.effective_decision.value,
                    run.decision.summary if run.decision else (run.error or ""),
                    caso.reason,
                )
            )

    medidos = sum(finales.values()) - (1 if interrumpida else 0)
    return {
        "medidos": medidos,
        "total": len(casos),
        "aciertos": aciertos,
        "fieles": fieles,
        "citas": (citas_validas, citas_totales),
        "base_aciertos": base_aciertos,
        "finales": finales,
        "desacuerdos": desacuerdos,
        "interrumpida": interrumpida,
    }


def informar(datos: dict, split: str, modelo: str) -> None:
    medidos = datos["medidos"]
    print("\n" + "=" * ANCHO)
    print(f"RESULTADO ({split}, modelo {modelo})")
    print("=" * ANCHO)

    if medidos == 0:
        print("  No se midio ningun caso.")
        return

    print(f"  casos medidos        {medidos} de {datos['total']}")
    print(f"  agente               {datos['aciertos']}/{medidos} aciertos")
    print(f"  linea base           {datos['base_aciertos']}/{datos['total']} aciertos")
    print(f"  explicaciones fieles {datos['fieles']}/{medidos}")
    validas, totales = datos["citas"]
    if totales:
        print(f"  citas verificadas    {validas}/{totales} correctas")

    print("\n  Como acabo cada vuelta:")
    for final, cuantas in sorted(datos["finales"].items()):
        print(f"    {final:20} {cuantas}")

    if datos["desacuerdos"]:
        print("\n  Donde el agente no decidio lo esperado:")
        for caso_id, esperado, obtenido, dijo, motivo in datos["desacuerdos"]:
            print(f"\n    {caso_id}")
            print(f"      esperado {esperado}, decidio {obtenido}")
            print(f"      el agente dijo: {dijo[:200]}")
            print(f"      por que se espera otra cosa: {motivo[:250]}")

    if datos["interrumpida"]:
        print(
            "\n  AVISO: la tanda se corto por falta de cupo. Los numeros de arriba\n"
            "  son de los casos que si se midieron y no son comparables con una\n"
            "  tanda completa."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--final",
        action="store_true",
        help="mide sobre el reservado. Lo quema: solo cuando el prompt este fijo.",
    )
    parser.add_argument("--modelo", default=settings.gemini_model)
    args = parser.parse_args()

    split = HOLDOUT if args.final else CALIBRATION
    casos = sorted(load_cases(split, final_measurement=args.final), key=lambda c: c.id)

    modelo = GeminiClient(
        api_key=settings.gemini_api_key,
        model=args.modelo,
        cache=ResponseCache(),
    )

    senales = preparar(casos)
    faltantes = contar_coste(casos, senales, modelo)
    disponibles = modelo.budget.remaining(args.modelo)

    print("\n" + "=" * ANCHO)
    print(f"PLAN ({split}, modelo {args.modelo})")
    print("=" * ANCHO)
    print(f"  casos                {len(casos)}")
    print(f"  ya en cache          {len(casos) - len(faltantes)} (no gastan nada)")
    print(f"  peticiones necesarias {len(faltantes)}")
    print(
        f"  cupo restante hoy    "
        f"{'sin tope' if disponibles is None else disponibles}"
    )

    if disponibles is not None and len(faltantes) > disponibles:
        print(
            f"\n  No alcanza: hacen falta {len(faltantes)} peticiones y quedan "
            f"{disponibles}.\n"
            "  No se empieza. Una tanda a medias no da una medida parcial, da unos\n"
            "  casos medidos y otros escalados por falta de cupo, y ese numero no\n"
            "  significa nada aunque lo parezca.\n"
            "\n  Faltan por pedir: " + ", ".join(c.id for c in faltantes[:12])
        )
        print(
            "\n  Opciones: esperar al reinicio diario del cupo, o repetir con\n"
            "  --modelo OTRO, porque el cupo se cuenta por modelo dentro del\n"
            "  proyecto de Google. Cambiar de modelo cambia el sistema medido y\n"
            "  hay que decirlo al publicar el numero."
        )
        return 1

    datos = evaluar(casos, senales, modelo)
    informar(datos, split, args.modelo)

    print(
        f"\n  Muestra: {datos['total']} casos sinteticos de una sola identidad, con la\n"
        "  decision correcta anotada a mano. Sirve para comparar al agente con la\n"
        "  linea base sobre el mismo material, no para prever que hara con\n"
        "  documentos reales."
    )
    if split == CALIBRATION:
        print(
            "\n  Esto es calibracion: es donde se ajusta el prompt. El numero que se\n"
            "  publica es el del reservado, y solo se mide una vez."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
