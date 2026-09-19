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
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.evaluation.catalog import TODAY, load_cases
from app.evaluation.split import CALIBRATION, HOLDOUT
from app.signals.pipeline import build_signals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelo", default=settings.gemini_model)
    parser.add_argument(
        "--caso",
        default=None,
        help="identificador del caso; por defecto el primero de calibracion",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help=(
            "sondea con un caso del reservado. Solo tiene sentido cuando la "
            "medicion final ya esta lanzada y hay que recuperar los casos "
            "que se cayeron."
        ),
    )
    args = parser.parse_args()

    split = HOLDOUT if args.final else CALIBRATION
    casos = sorted(
        load_cases(split, final_measurement=args.final), key=lambda c: c.id
    )

    if args.caso is None:
        caso = casos[0]
    else:
        # Antes esto era un `next(..., casos[0])` que ante un identificador
        # desconocido se callaba y sondeaba el primero de calibracion. Como
        # ese suele estar en cache, la sonda salia gratis y respondia "el
        # modelo contesta" sin haber tocado la API: exactamente la mentira
        # que esta herramienta existe para no contar. Una errata al teclear
        # el nombre bastaba.
        caso = next((c for c in casos if c.id == args.caso), None)
        if caso is None:
            print(f"No hay ningun caso {args.caso!r} en {split}.")
            if not args.final:
                print("Si es del conjunto reservado, anadir --final.")
            print("Los de aqui son:")
            for c in casos:
                print(f"  {c.id}")
            return 2


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

    # Una sonda sobre un caso ya cacheado no toca la red, asi que
    # contestaria "el modelo responde" sin haberlo preguntado. Es el
    # mismo enganio que el de un --caso mal tecleado, por otra puerta.
    if modelo.is_cached(build_prompt(senales), DECISION_RESPONSE_SCHEMA):
        print()
        print(f"AVISO: {caso.id} ya esta en cache para {args.modelo}.")
        print("Lo que salga sale de disco y NO dice si el modelo responde")
        print("hoy. Para sondear de verdad, elegir un caso sin cachear.")

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
    elif run.outcome is RunOutcome.BAD_CREDENTIAL:
        # El diagnostico correcto aqui vale una sesion: con el mensaje
        # generico de abajo se concluye que Gemini esta caido y se van a
        # mirar los modelos, cuando lo que pasa es que la clave no vale.
        print(f"error:     {(run.error or '')[:400]}")
        print()
        print("La credencial no vale. Esto NO dice nada sobre si Gemini responde")
        print("hoy ni sobre el cupo, que sigue intacto: la peticion no llego al")
        print("modelo y no se ha contado.")
        print()
        print("  1. Poner una clave de Google AI Studio en .env (aistudio.google.com/apikey).")
        print("  2. Rehacer el contenedor: docker compose up -d --force-recreate api.")
        print("     Editar .env no basta, la variable se inyecta al crearlo.")
        print("  3. Repetir esta sonda.")
    else:
        print(f"error:     {(run.error or '')[:400]}")
        print()
        print("El modelo no contesto. No conviene lanzarle una tanda hoy: lo que")
        print("se gastaria en ella no volveria en forma de medida.")

    print(f"cupo restante: {modelo.budget.remaining(args.modelo)}")
    return 0 if run.outcome is RunOutcome.DECIDED else 1


if __name__ == "__main__":
    sys.exit(main())
