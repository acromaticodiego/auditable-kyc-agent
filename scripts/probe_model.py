"""Gasta UNA peticion para ver si un modelo contesta, antes de fiarle una tanda.

Existe por un incidente concreto: se lanzo una tanda de 10 casos contra un
modelo del que no se sabia nada y devolvio 503 en ocho de ellos.  Se fue el
cupo entero de ese modelo sin medir un solo caso.

Una peticion de las 20 diarias es barata comparada con diez, y convierte la
pregunta "¿este modelo responde hoy?" de conjetura en dato.  El caso que se
usa es uno real del conjunto de calibracion y no un ejemplo de juguete, asi
que si el modelo contesta la respuesta queda en cache y la tanda posterior
ya no la paga: la sonda sale gratis si el modelo funciona.

    docker compose exec api python scripts/probe_model.py --modelo gemini-3.7-flash
"""

from __future__ import annotations

import argparse
import sys

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.runner import RunOutcome, run_agent
from app.config import settings
from app.evaluation.catalog import TODAY, load_cases
from app.signals.pipeline import build_signals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", default=settings.gemini_model)
    parser.add_argument(
        "--caso",
        default=None,
        help="identificador del caso; por defecto el primero de calibracion",
    )
    args = parser.parse_args()

    casos = sorted(load_cases(), key=lambda c: c.id)
    caso = next((c for c in casos if c.id == args.caso), casos[0])

    modelo = GeminiClient(
        api_key=settings.gemini_api_key,
        model=args.modelo,
        cache=ResponseCache(),
        # Sin reintento: la sonda pregunta si el modelo responde AHORA.
        # Reintentar la convertiria en dos peticiones y ademas taparia
        # justo lo que se quiere ver, que es si el 503 es la norma del dia.
        max_retries=0,
    )

    print(f"modelo:  {args.modelo}")
    print(f"caso:    {caso.id}")
    print(f"cupo:    {modelo.budget.remaining(args.modelo)} peticiones restantes")
    ajenas = modelo.budget.spent_by_other_keys(args.modelo)
    if ajenas:
        plural = "peticion" if ajenas == 1 else "peticiones"
        print(f"aviso:   otra clave ya gasto {ajenas} {plural} de este modelo hoy.")
        print("         El cupo va por proyecto de Google, no por clave, asi que")
        print("         rotar la credencial no lo recupera si comparten proyecto.")
    print("Midiendo senales (sin tocar la API)...")

    senales = build_signals(*caso.build(), today=TODAY)
    run = run_agent(senales, modelo)

    print()
    print(f"resultado: {run.outcome.value}")
    if run.outcome is RunOutcome.DECIDED:
        print(f"decision:  {run.decision.decision.value}")
        print(f"esperado:  {caso.expected_decision.value}")
        print(f"resumen:   {run.decision.summary}")
        print(f"citas:     {sum(1 for r in run.audit.results if r.valid)}"
              f"/{len(run.audit.results)} verificadas correctas")
        for resultado in run.audit.invalid:
            print(f"  cita falsa: {resultado.signal_id} dijo "
                  f"{resultado.cited_value!r}, valia {resultado.actual_value!r} "
                  f"({resultado.status.value})")
        print()
        print("El modelo contesta. La respuesta queda en cache, asi que la tanda")
        print("posterior no vuelve a pagar este caso.")
    else:
        print(f"error:     {(run.error or '')[:400]}")
        print()
        print("El modelo no contesto. No conviene lanzarle una tanda hoy: lo que")
        print("se gastaria en ella no volveria en forma de medida.")

    print(f"cupo restante: {modelo.budget.remaining(args.modelo)}")
    return 0 if run.outcome is RunOutcome.DECIDED else 1


if __name__ == "__main__":
    sys.exit(main())
