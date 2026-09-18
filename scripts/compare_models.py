"""Compara las decisiones de varios modelos, leyendo solo la cache.

**No gasta ni una peticion.** Se limita a mirar lo que ya se pidio alguna
vez, lo que permite recuperar comparaciones que de otro modo se quedarian
en la memoria de quien ejecuto la tanda.

POR QUE ESTO IMPORTA
--------------------

Todo el proyecto mide "el agente" como si fuera una cosa, y no lo es: el
prompt es nuestro, pero la decision la toma un modelo concreto.  La primera
vez que se midieron dos modelos con el MISMO prompt aparecieron desacuerdos
en los casos ambiguos, y eso cambia como hay que leer cualquier cifra de
acierto: parte de ella es del prompt y parte del modelo que se eligio.

La cache se indexa por (modelo, peticion), asi que las respuestas de cada
modelo conviven sin pisarse y se pueden enfrentar despues. Un caso que un
modelo nunca contesto sale como hueco, no como acuerdo: dar por supuesto
que habria coincidido seria inventarse la mitad de la comparacion.

    docker compose exec api python scripts/compare_models.py
    docker compose exec api python scripts/compare_models.py --modelos a b c
"""

from __future__ import annotations

import argparse
import json
import sys

from pydantic import ValidationError

from app.agent.cache import ResponseCache
from app.agent.gemini import _extract_text
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.domain.decision import AgentDecision
from app.evaluation.catalog import TODAY, load_cases
from app.signals.pipeline import build_signals

MODELOS_POR_DEFECTO = ("gemini-3.5-flash", "gemini-3.1-flash-lite")
ANCHO = 100


def peticion(prompt: str) -> dict:
    """La misma forma que arma el cliente, para dar con la clave de cache."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": DECISION_RESPONSE_SCHEMA,
            "temperature": 0.0,
        },
    }


def decision_cacheada(cache: ResponseCache, modelo: str, prompt: str) -> str | None:
    """La decision que ese modelo dio, o None si nunca se le pregunto."""
    guardado = cache.get(ResponseCache.key(modelo, peticion(prompt)))
    if guardado is None:
        return None
    try:
        return AgentDecision.model_validate(
            json.loads(_extract_text(guardado))
        ).decision.value
    except (ValidationError, ValueError, KeyError):
        # Una respuesta guardada que no cumple el contrato es informacion,
        # no un error: significa que ese modelo rompio el contrato ese dia.
        return "(incumplio el contrato)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelos", nargs="+", default=list(MODELOS_POR_DEFECTO))
    args = parser.parse_args()

    casos = sorted(load_cases(), key=lambda c: c.id)
    cache = ResponseCache()

    print(f"Leyendo la cache de {len(args.modelos)} modelos. No gasta cupo.\n")
    cabecera = "caso".ljust(46) + "esperado".ljust(22)
    for modelo in args.modelos:
        cabecera += modelo.replace("gemini-", "").ljust(20)
    print(cabecera)
    print("-" * ANCHO)

    aciertos = {m: 0 for m in args.modelos}
    contestados = {m: 0 for m in args.modelos}
    discrepan: list[str] = []

    for caso in casos:
        prompt = build_prompt(build_signals(*caso.build(), today=TODAY))
        fila = caso.id.ljust(46) + caso.expected_decision.value.ljust(22)
        vistas: list[str | None] = []

        for modelo in args.modelos:
            decision = decision_cacheada(cache, modelo, prompt)
            vistas.append(decision)
            if decision is None:
                fila += "-".ljust(20)
                continue
            contestados[modelo] += 1
            correcto = decision == caso.expected_decision.value
            aciertos[modelo] += correcto
            marca = "ok" if correcto else "XX"
            fila += f"{decision[:16]} {marca}".ljust(20)

        print(fila)
        respondidas = [v for v in vistas if v is not None]
        if len(respondidas) > 1 and len(set(respondidas)) > 1:
            discrepan.append(caso.id)

    print(f"\n{'=' * ANCHO}")
    print("ACIERTOS, cada uno sobre los casos que ESE modelo contesto")
    print("=" * ANCHO)
    for modelo in args.modelos:
        total = contestados[modelo]
        if total:
            print(f"  {modelo:26} {aciertos[modelo]}/{total}")
        else:
            print(f"  {modelo:26} (ninguno en cache)")
    print(
        "\n  Los denominadores son distintos y por eso no se comparan entre si:\n"
        "  cada modelo contesto los casos que contesto. Lo comparable es el\n"
        "  detalle de abajo, donde los dos respondieron."
    )

    if discrepan:
        print(f"\n{'=' * ANCHO}")
        print("DONDE LOS MODELOS NO SE PONEN DE ACUERDO")
        print("=" * ANCHO)
        print(
            "  Mismo prompt, mismas senales, decisiones distintas. Parte del\n"
            "  acierto que este proyecto llama 'del agente' es del modelo.\n"
        )
        for caso_id in discrepan:
            print(f"  - {caso_id}")
    else:
        print("\n  Ningun caso con respuesta de mas de un modelo discrepa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
