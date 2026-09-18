"""Evalua al agente sobre el conjunto de casos y lo compara con la linea base.

Por defecto solo mira **calibracion**, que es donde se puede iterar sobre el
prompt.  El reservado se pide con `--final` y esa ejecucion lo quema: a
partir de ahi, tocar el prompt mirando ese resultado convertiria la
siguiente medicion en otro numero elegido sobre sus propios datos.

El script cuenta lo que va a costar ANTES de gastar nada.  Cada caso que ya
esta en cache sale gratis; los que no, valen una peticion de las 20 del
dia.  Si no alcanza, no empieza: dejar una tanda a medias no da una medida
parcial, da doce casos de los que solo tres se midieron y nueve sin medir,
que es un numero sin sentido que ademas invita a leerse como si lo tuviera.

La primera version de esa cuenta estaba mal y salio caro.  Contaba una
peticion por caso, pero el cliente reintenta una vez ante un 503, asi que
un caso podia costar dos.  Una tanda que el plan cifro en 10 peticiones
gasto 22 y no midio ni un solo caso: ocho 503 seguidos, cada uno cobrado
dos veces, y un 429 al final.  De ahi salen las dos reglas que hay abajo.

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
from app.domain.signals import SignalSet
from app.evaluation.baseline import decide as decide_baseline
from app.evaluation.catalog import TODAY, Case, load_cases
from app.evaluation.rehearsal import MODELO_DE_ENSAYO, ModeloDeEnsayo
from app.evaluation.split import CALIBRATION, HOLDOUT
from app.signals.pipeline import build_signals

ANCHO = 78


def preparar(casos: list[Case]) -> dict[str, SignalSet]:
    """Calcula las senales de cada caso.  Es lento pero no gasta cupo."""
    print(f"Midiendo senales de {len(casos)} casos (OCR, sin tocar la API)...")
    return {caso.id: build_signals(*caso.build(), today=TODAY) for caso in casos}


def contar_coste(
    casos: list[Case], senales: dict[str, SignalSet], modelo: GeminiClient
) -> list[Case]:
    """Los casos que exigiran una peticion de verdad."""
    return [
        caso
        for caso in casos
        if not modelo.is_cached(
            build_prompt(senales[caso.id]), DECISION_RESPONSE_SCHEMA
        )
    ]


def coste_maximo(faltantes: list[Case], modelo: GeminiClient) -> int:
    """Lo que puede llegar a costar la tanda, no lo que costaria si todo va bien.

    Regla 1 de las dos que dejo el incidente: la cuenta que decide si se
    empieza tiene que ser la del peor caso.  Un 503 consume cupo igual que
    una respuesta buena, asi que cada reintento es una peticion mas que
    nadie habia presupuestado.
    """
    return len(faltantes) * (modelo.max_retries + 1)


def evaluar(
    casos: list[Case], senales: dict[str, SignalSet], modelo: GeminiClient
) -> dict:
    """Recorre los casos y separa lo que es del agente de lo que es del proveedor.

    La distincion no es cosmetica.  Un JSON que incumple el contrato SI es
    un fallo del agente y cuenta en su contra, porque es el modelo el que
    razono mal.  Un 503 no: ahi el agente no llego a opinar, y meterlo en el
    denominador mediria la salud de la infraestructura de Google y lo
    llamaria acierto del prompt.

    Para el sistema en produccion los dos acaban igual, en revision humana
    (ver docs/adr/0004).  Para medir al agente son cosas distintas.
    """
    aciertos = 0
    # Casos donde la decision del sistema coincide con la esperada pero NO
    # la tomo el agente: rompio el contrato y lo que coincidio fue el
    # fallback a revision humana.
    #
    # Se cuentan aparte porque contarlos como aciertos premia al agente por
    # una coincidencia. Paso de verdad: en un caso cuya decision correcta
    # era escalar, el modelo devolvio una respuesta invalida, el sistema
    # escalo por defecto y el informe lo apunto como acierto del agente.
    coincidencias = 0
    fieles = 0
    completas = 0
    # Cuantos casos tenian de verdad algo que omitir. Sin este numero, la
    # completitud de un conjunto sin senales adversas sale perfecta sin
    # merito, y el porcentaje solo pareceria bueno.
    con_algo_que_omitir = 0
    citas_totales = 0
    citas_validas = 0
    contestados = 0
    perdidos: list[str] = []
    finales: Counter[str] = Counter()
    desacuerdos: list[tuple[str, str, str, str, str]] = []
    base_aciertos = 0
    # La linea base sobre los MISMOS casos que el agente llego a contestar.
    #
    # Sin esto la comparacion es tramposa sin querer: el primer informe puso
    # 6/8 del agente al lado de 9/12 de la linea base, que son denominadores
    # distintos sobre casos distintos, y de ahi no se puede concluir nada.
    # Da la casualidad de que sobre los mismos ocho la linea base tambien
    # sacaba 6, o sea que el titular que sugeria el informe estaba al reves.
    base_en_contestados = 0
    interrumpida = False

    print()
    print("caso".ljust(38) + "esperado".ljust(22) + "agente".ljust(22) + "base")
    print("-" * ANCHO)

    for caso in casos:
        conjunto = senales[caso.id]
        base = decide_baseline(conjunto)
        base_ok = base.decision is caso.expected_decision
        base_aciertos += base_ok
        marca_base = "ok" if base_ok else "XX"

        run = run_agent(conjunto, modelo)

        if run.outcome is RunOutcome.OUT_OF_QUOTA:
            print()
            print(f"Se acabo el cupo en {caso.id}. Se para la tanda.")
            print("  " + (run.error or "")[:300])
            interrumpida = True
            break

        finales[run.outcome.value] += 1

        if run.outcome is RunOutcome.UNAVAILABLE:
            perdidos.append(caso.id)
            print(
                caso.id.ljust(38)
                + "(el proveedor no respondio)".ljust(44)
                + marca_base
            )
            continue

        contestados += 1
        base_en_contestados += base_ok
        coincide = run.effective_decision is caso.expected_decision
        decidio = run.outcome is RunOutcome.DECIDED
        agente_ok = coincide and decidio
        aciertos += agente_ok
        coincidencias += coincide and not decidio
        fieles += run.faithful
        completas += run.complete
        if run.completeness is not None and run.completeness.adverse:
            con_algo_que_omitir += 1
        if run.audit is not None:
            citas_totales += len(run.audit.results)
            citas_validas += sum(1 for r in run.audit.results if r.valid)

        print(
            caso.id.ljust(38)
            + caso.expected_decision.value.ljust(22)
            + run.effective_decision.value.ljust(22)
            + marca_base.ljust(6)
            + ("ok" if agente_ok else ("~~" if coincide else "XX"))
        )

        if not agente_ok:
            desacuerdos.append(
                (
                    caso.id,
                    caso.expected_decision.value,
                    run.effective_decision.value,
                    run.decision.summary if run.decision else (run.error or "")[:200],
                    caso.reason,
                )
            )

    return {
        "contestados": contestados,
        "perdidos": perdidos,
        "total": len(casos),
        "aciertos": aciertos,
        "fieles": fieles,
        "citas": (citas_validas, citas_totales),
        "coincidencias": coincidencias,
        "completas": completas,
        "con_algo_que_omitir": con_algo_que_omitir,
        "base_aciertos": base_aciertos,
        "base_en_contestados": base_en_contestados,
        "finales": finales,
        "desacuerdos": desacuerdos,
        "interrumpida": interrumpida,
    }


def informar(datos: dict, split: str, modelo: str) -> None:
    contestados = datos["contestados"]
    total = datos["total"]

    print()
    print("=" * ANCHO)
    print(f"RESULTADO ({split}, modelo {modelo})")
    print("=" * ANCHO)

    if contestados != total:
        # Va arriba y no en una nota al pie a proposito: quien lee esto de
        # reojo tiene que tropezarse con el aviso antes que con el numero.
        faltan = total - contestados
        print(f"  TANDA INCOMPLETA: el agente contesto {contestados} de {total} casos.")
        print(f"  Los {faltan} restantes no los midio nadie, asi que lo de abajo NO es")
        print("  la medida del agente sobre este conjunto: es lo que se sabe hasta")
        print("  ahora. Las respuestas buenas quedaron en cache, asi que repetir la")
        print("  tanda cuando haya cupo solo paga por las que falten.")
        print()

    if contestados == 0:
        print("  No se midio ningun caso.")
        if datos["perdidos"]:
            print("  El proveedor no respondio en: " + ", ".join(datos["perdidos"]))
        return

    # Los dos primeros numeros van juntos y sobre los mismos casos porque es
    # la unica comparacion que significa algo. Poner el acierto del agente
    # sobre los casos que contesto al lado del de la linea base sobre el
    # conjunto entero compara denominadores distintos sobre casos distintos.
    print(f"  Sobre los {contestados} casos que el agente contesto:")
    print(f"    agente             {datos['aciertos']}/{contestados} aciertos")
    if datos["coincidencias"]:
        print(
            f"      (+{datos['coincidencias']} donde el sistema acerto pero el "
            "agente no decidio:"
        )
        print(
            "       rompio el contrato y coincidio el fallback. No cuentan "
            "como acierto suyo,"
        )
        print("       y en la tabla salen marcados ~~ en vez de ok)")
    print(f"    linea base         {datos['base_en_contestados']}/{contestados} aciertos")
    print(f"    explicaciones fieles     {datos['fieles']}/{contestados}")
    print(f"    explicaciones completas  {datos['completas']}/{contestados}")
    validas, totales = datos["citas"]
    if totales:
        print(f"    citas verificadas        {validas}/{totales} correctas")
    print(
        f"    de esos casos, {datos['con_algo_que_omitir']} tenian alguna senal "
        "adversa que citar"
    )
    print(
        "    (fiel = nada de lo que dijo es falso; completa = no se callo "
        "ninguna adversa)"
    )

    print()
    print(
        f"  De referencia, la linea base sobre el conjunto entero: "
        f"{datos['base_aciertos']}/{total}."
    )
    print("  No gasta peticiones, asi que siempre se puede medir completa. No es")
    print("  comparable con el numero del agente de arriba si faltan casos.")

    if datos["perdidos"]:
        print()
        print(
            f"  Sin respuesta del proveedor ({len(datos['perdidos'])}): "
            + ", ".join(datos["perdidos"])
        )
        print("  No cuentan ni a favor ni en contra del agente: ahi no llego a")
        print("  opinar. En produccion acabarian en revision humana igualmente.")

    print()
    print("  Como acabo cada vuelta:")
    for final, cuantas in sorted(datos["finales"].items()):
        print(f"    {final:20} {cuantas}")

    if datos["desacuerdos"]:
        print()
        print("  Donde el agente no decidio lo esperado:")
        for caso_id, esperado, obtenido, dijo, motivo in datos["desacuerdos"]:
            print()
            print(f"    {caso_id}")
            print(f"      esperado {esperado}, decidio {obtenido}")
            print(f"      el agente dijo: {dijo[:200]}")
            print(f"      por que se espera otra cosa: {motivo[:250]}")

    if datos["interrumpida"]:
        print()
        print("  La tanda se corto por falta de cupo antes de acabar.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--final",
        action="store_true",
        help="mide sobre el reservado. Lo quema: solo cuando el prompt este fijo.",
    )
    parser.add_argument("--modelo", default=settings.gemini_model)
    parser.add_argument(
        "--simulacro",
        action="store_true",
        help=(
            "ensaya la tanda entera sin llamar a la API. No mide nada: "
            "comprueba que el arnes funciona antes de gastar el cupo."
        ),
    )
    args = parser.parse_args()

    if args.simulacro and args.final:
        # Un ensayo no es una medicion final, y --final es precisamente la
        # declaracion de que lo que sale se publica. Juntarlos deja el
        # reservado marcado como usado a cambio de un numero que no mide
        # nada, que es el peor intercambio posible.
        parser.error(
            "--simulacro y --final se contradicen: un ensayo no puede ser la "
            "medicion final. Ensaya sobre calibracion y deja el reservado."
        )

    split = HOLDOUT if args.final else CALIBRATION
    casos = sorted(load_cases(split, final_measurement=args.final), key=lambda c: c.id)
    nombre_del_modelo = MODELO_DE_ENSAYO if args.simulacro else args.modelo

    senales_adelantadas: dict[str, SignalSet] | None = None
    if args.simulacro:
        # Las senales se calculan antes de construir el doble porque el doble
        # se indexa por el prompt, y el prompt sale de las senales.
        casos_ordenados = casos
        senales_adelantadas = preparar(casos_ordenados)

    modelo = (
        ModeloDeEnsayo(senales_adelantadas)
        if senales_adelantadas is not None
        else GeminiClient(
        api_key=settings.gemini_api_key,
        model=args.modelo,
        cache=ResponseCache(),
        # Regla 2 de las que dejo el incidente: en una tanda no se reintenta.
        #
        # Fuera de una tanda, reintentar un 503 es razonable.  Dentro, el
        # reintento se paga con el cupo que necesitan los casos que aun no
        # se han medido, y ese cambio no es neutral: con doce casos y un
        # reintento el peor caso son 24 peticiones sobre un tope de 20, o
        # sea que la tanda completa no cabe ni empezando con el cupo
        # intacto.  Sin reintento caben las doce.
        #
        # Lo que se pierde es poco: un caso que se cae queda sin medir, y
        # como las respuestas buenas se guardan en cache, repetir la tanda
        # manana solo paga por las que falten.
        max_retries=0,
        )
    )

    senales = senales_adelantadas if senales_adelantadas is not None else preparar(casos)
    faltantes = contar_coste(casos, senales, modelo)
    peor_caso = coste_maximo(faltantes, modelo)
    disponibles = modelo.budget.remaining(nombre_del_modelo)

    print()
    print("=" * ANCHO)
    print(f"PLAN ({split}, modelo {nombre_del_modelo})")
    print("=" * ANCHO)
    print(f"  casos                 {len(casos)}")
    print(f"  ya en cache           {len(casos) - len(faltantes)} (no gastan nada)")
    print(f"  peticiones necesarias {len(faltantes)}")
    print(
        f"  peor caso             {peor_caso} "
        f"(con {modelo.max_retries} reintentos; un 503 tambien gasta cupo)"
    )
    print(
        "  cupo restante hoy     "
        + ("sin tope" if disponibles is None else str(disponibles))
    )

    ajenas = modelo.budget.spent_by_other_keys(nombre_del_modelo)
    if ajenas:
        print()
        plural = "peticion" if ajenas == 1 else "peticiones"
        print(f"  AVISO: hoy se han gastado {ajenas} {plural} de este modelo con")
        print("  OTRA clave. El cupo gratuito va por proyecto de Google y no por")
        print("  clave, asi que si las dos pertenecen al mismo proyecto ese saldo")
        print("  NO se recupera rotando la credencial: el contador local dira que")
        print("  quedan 20 y Google respondera 429 a la primera peticion.")

    if disponibles is not None and peor_caso > disponibles:
        print()
        print(
            f"  No alcanza: en el peor caso hacen falta {peor_caso} peticiones y "
            f"quedan {disponibles}."
        )
        print("  No se empieza. Una tanda a medias no da una medida parcial, da unos")
        print("  casos medidos y otros sin medir, y ese numero no significa nada")
        print("  aunque lo parezca.")
        print()
        print("  Faltan por pedir: " + ", ".join(c.id for c in faltantes[:12]))
        print()
        print("  Opciones: esperar al reinicio del cupo (medianoche del Pacifico), o")
        print("  repetir con --modelo OTRO, porque el cupo se cuenta por modelo")
        print("  dentro del proyecto de Google. Cambiar de modelo cambia el sistema")
        print("  medido y hay que decirlo al publicar el numero.")
        return 1

    datos = evaluar(casos, senales, modelo)
    informar(datos, split, nombre_del_modelo)

    print()
    print(f"  Muestra: {datos['total']} casos sinteticos de una sola identidad, con la")
    print("  decision correcta anotada a mano. Sirve para comparar al agente con la")
    print("  linea base sobre el mismo material, no para prever que hara con")
    print("  documentos reales.")
    if split == CALIBRATION:
        print()
        print("  Esto es calibracion: es donde se ajusta el prompt. El numero que se")
        print("  publica es el del reservado, y solo se mide una vez.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
