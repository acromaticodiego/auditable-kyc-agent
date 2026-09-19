"""Una pantalla que ensena lo que distingue a este sistema: la auditoria.

Un expediente en JSON demuestra que las citas se verifican, pero hay que
leerselo entero para verlo.  La columna de esta pantalla que dice, cita por
cita, si lo que el agente afirmo era cierto, se entiende de un vistazo. Es
lo unico que no tiene cualquier demo de "LLM decide algo".

DOS DECISIONES QUE MANDAN SOBRE TODO LO DEMAS
---------------------------------------------

**No gasta cupo. Nunca.**  Esta pantalla sirve exclusivamente respuestas
que ya estan en cache, y cuando un caso no lo esta se niega y lo dice, en
vez de pedirlo.  El motivo es que una demo se abre delante de alguien y se
pulsa varias veces; con veinte peticiones al dia, una pantalla que decide
de verdad se come la medicion del dia en dos clics.  Aqui el riesgo no es
teorico: ya se perdieron peticiones por lanzar una herramienta creyendo
que solo miraba.

**Lo que se ve son decisiones reales del modelo, no un doble.**  Salen de
cache, pero las produjo `gemini-3.5-flash` ante ese mismo juego de senales.
Habria sido mas comodo enchufar el doble de ensayo -responde siempre, a
cualquier cosa- y habria convertido la pantalla en una mentira bonita: lo
que se enseria serian las reglas fijas de la linea base disfrazadas de
agente.

EL BOTON DE ROMPER EL DOCUMENTO
-------------------------------

Ensena lo que un marcador no puede: que la decision se mueve **porque se
movio una senal**, y que el cambio se puede rastrear hasta el cotejo
concreto que paso a `mismatch`.

No muta la imagen en vivo, que costaria una peticion nueva.  Salta al caso
del catalogo que ya representa ese documento retocado, y lo dice.  El
catalogo existe precisamente para eso: cada pareja es el mismo documento
con y sin el retoque.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.runner import RunOutcome, run_agent
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.config import settings
from app.domain.citation_audit import CitationStatus
from app.evaluation.catalog import TODAY, load_cases
from app.signals.pipeline import build_signals

router = APIRouter(tags=["demo"])

PAGINA = Path(__file__).resolve().parent.parent / "web" / "index.html"

# Parejas del catalogo que son el mismo documento con y sin retoque.  Es lo
# que hace honesto el boton: no se inventa una mutacion, se salta al caso
# que ya la representa y que ya se midio.
PAREJAS = {
    "legitimo-torcido": ("ambiguo-apellido-difiere-una-letra", "el apellido"),
    "legitimo-jpeg-moderado": ("fraude-fecha-nacimiento-retocada", "la fecha de nacimiento"),
}
# La vuelta atras, para que el boton funcione en los dos sentidos.
PAREJAS_INVERSAS = {roto: (limpio, que) for limpio, (roto, que) in PAREJAS.items()}


def get_demo_client() -> GeminiClient:
    """El cliente, como dependencia para que los tests puedan sustituirlo.

    Aqui importa mas que en el otro router: lo que hay que poder probar es
    justamente que esta pantalla NO pide nada, y con el cliente real la
    prueba dependeria de que la cache del disco tuviera o no el caso.
    """
    return GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        cache=ResponseCache(),
    )


def _senal_a_json(senal) -> dict:
    return {
        "id": senal.id,
        "tipo": senal.kind.value,
        "descripcion": senal.description,
        "valor": senal.value,
        "disponible": senal.available,
        "motivo_no_disponible": senal.unavailable_reason,
    }


@router.get("/", include_in_schema=False)
def pagina() -> FileResponse:
    return FileResponse(PAGINA, media_type="text/html")


@router.get(
    "/demo/casos",
    summary="Los casos que la pantalla puede ensenar sin gastar cupo",
)
def listar_casos(modelo: GeminiClient = Depends(get_demo_client)) -> dict:
    """Solo calibracion.

    El reservado se queda fuera aunque tambien tenga casos en cache. No es
    por el cupo -de cache sale gratis- sino porque una pantalla que invita
    a pulsar es la forma mas facil de acabar mirando el reservado "solo
    para ver como va", que es justo lo que la particion existe para
    impedir.
    """
    casos = []
    for caso in sorted(load_cases(), key=lambda c: c.id):
        senales = build_signals(*caso.build(), today=TODAY)
        casos.append(
            {
                "id": caso.id,
                "esperado": caso.expected_decision.value,
                "en_cache": modelo.is_cached(
                    build_prompt(senales), DECISION_RESPONSE_SCHEMA
                ),
                "pareja": PAREJAS.get(caso.id, PAREJAS_INVERSAS.get(caso.id)),
            }
        )
    return {"modelo": settings.gemini_model, "casos": casos}


@router.get(
    "/demo/casos/{caso_id}",
    summary="El expediente completo de un caso, con cada cita contrastada",
)
def ver_caso(
    caso_id: str, modelo: GeminiClient = Depends(get_demo_client)
) -> dict:
    caso = next((c for c in load_cases() if c.id == caso_id), None)
    if caso is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no hay ningun caso {caso_id!r} en calibracion",
        )

    senales = build_signals(*caso.build(), today=TODAY)

    # La puerta. Sin esto, abrir la pantalla en un caso sin cachear
    # lanzaria una peticion de verdad, y una demo se pulsa muchas veces.
    if not modelo.is_cached(build_prompt(senales), DECISION_RESPONSE_SCHEMA):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{caso_id} no esta en cache para {settings.gemini_model}. Esta "
                "pantalla no pide nada al modelo: medir un caso nuevo se hace "
                "con scripts/evaluate_agent.py --gastar, que ensena antes lo "
                "que va a costar."
            ),
        )

    run = run_agent(senales, modelo)

    if run.outcome is not RunOutcome.DECIDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"la respuesta guardada de {caso_id} no produjo decision: {run.error}",
        )

    por_senal = {r.signal_id: r for r in run.audit.results}
    fundamentos = []
    for fundamento in run.decision.groundings:
        resultado = por_senal.get(fundamento.signal_id)
        fundamentos.append(
            {
                "senal": fundamento.signal_id,
                "peso": fundamento.weight.value,
                "texto": fundamento.text,
                "valor_citado": fundamento.cited_value,
                # Lo que hace distinta a esta pantalla: no se muestra lo que
                # el agente dijo, se muestra si era verdad.
                "verificada": resultado is not None and resultado.valid,
                "estado": resultado.status.value if resultado else "sin_contrastar",
                "valor_real": resultado.actual_value if resultado else None,
            }
        )

    # El salto va en los dos sentidos y el boton no puede decir lo mismo:
    # desde el documento intacto se rompe, desde el retocado se repara.
    if caso_id in PAREJAS:
        pareja, sentido = PAREJAS[caso_id], "romper"
    elif caso_id in PAREJAS_INVERSAS:
        pareja, sentido = PAREJAS_INVERSAS[caso_id], "reparar"
    else:
        pareja, sentido = None, None
    return {
        "caso": caso_id,
        "modelo": run.model,
        "esperado": caso.expected_decision.value,
        "decision": run.decision.decision.value,
        "acierta": run.decision.decision is caso.expected_decision,
        "resumen": run.decision.summary,
        "fundamentos": fundamentos,
        "auditoria": {
            "citas": len(run.audit.results),
            "validas": sum(1 for r in run.audit.results if r.valid),
            "fiel": run.audit.faithful,
            "completa": run.complete,
        },
        "senales": [_senal_a_json(s) for s in senales],
        "senales_totales": len(senales),
        "pareja": (
            {"caso": pareja[0], "que_cambia": pareja[1], "sentido": sentido}
            if pareja
            else None
        ),
        "estados_posibles": [e.value for e in CitationStatus],
    }
