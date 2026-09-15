"""Pruebas del bucle del agente con el modelo simulado.

Ninguna toca la API: el cupo es de 20 peticiones al dia y gastarlo en
comprobar que un JSON torcido se clasifica bien seria tirarlo.  Lo que
estas pruebas NO contestan es si el modelo real decide bien; eso lo mide
scripts/evaluate_agent.py sobre el conjunto de calibracion.

Lo que si se comprueba aqui, y es lo que importa, es que **ningun fallo
del modelo acabe en una aprobacion**.
"""

import json

import httpx
import pytest

from app.agent.budget import RequestBudget
from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.runner import FALLBACK_DECISION, AgentRun, RunOutcome, run_agent
from app.domain.decision import DecisionKind
from app.domain.signals import Signal, SignalKind, SignalSet

DECISION_VALIDA = {
    "decision": "approve",
    "summary": "Todo cuadra y la imagen deja leer los campos.",
    "groundings": [
        {
            "signal_id": "document.expired",
            "cited_value": "False",
            "weight": "in_favor",
            "text": "El documento sigue vigente a dia de hoy.",
        },
        {
            "signal_id": "facial.similarity",
            "cited_value": "0.89",
            "weight": "in_favor",
            "text": "La cara de la selfie y la del documento se parecen mucho.",
        },
    ],
}


def senales() -> SignalSet:
    return SignalSet(
        [
            Signal("facial.similarity", SignalKind.SCORE,
                   "Similitud entre la cara del documento y la de la selfie.",
                   value=0.89),
            Signal("document.expired", SignalKind.FLAG,
                   "El documento esta vencido a dia de hoy.", value=False),
            Signal("mrz.readable", SignalKind.FLAG,
                   "La MRZ del reverso se pudo leer.",
                   unavailable_reason="el recorte del reverso salio vacio"),
        ]
    )


def cliente(tmp_path, respuesta, *, status: int = 200, **kwargs) -> GeminiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=respuesta)
        cuerpo = {"candidates": [{"content": {"parts": [{"text": respuesta}]}}]}
        return httpx.Response(200, json=cuerpo)

    kwargs.setdefault(
        "budget", RequestBudget(tmp_path / "budget.json", daily_limit=None)
    )
    return GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-test",
        cache=ResponseCache(tmp_path),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda segundos: None,
        **kwargs,
    )


def test_una_respuesta_correcta_produce_decision_auditada(tmp_path):
    run = run_agent(senales(), cliente(tmp_path, json.dumps(DECISION_VALIDA)))

    assert run.outcome is RunOutcome.DECIDED
    assert run.decision.decision is DecisionKind.APPROVE
    assert run.effective_decision is DecisionKind.APPROVE
    # La auditoria se ejecuta sola: no hay forma de obtener una decision de
    # este bucle sin que sus citas hayan pasado por el verificador.
    assert run.audit is not None
    assert run.faithful


def test_una_cita_falsa_se_detecta_sin_invalidar_la_decision(tmp_path):
    """La auditoria informa, todavia no corrige; ver la cabecera del bucle.

    El dia que se decida degradar las decisiones infieles, este test tiene
    que cambiar a proposito y con un numero delante que lo justifique.
    """
    mentira = json.loads(json.dumps(DECISION_VALIDA))
    mentira["groundings"][1]["cited_value"] = "0.42"

    run = run_agent(senales(), cliente(tmp_path, json.dumps(mentira)))

    assert run.outcome is RunOutcome.DECIDED
    assert run.effective_decision is DecisionKind.APPROVE
    assert not run.faithful
    assert [r.signal_id for r in run.audit.invalid] == ["facial.similarity"]


def test_citar_una_senal_que_no_se_pudo_medir_es_fiel(tmp_path):
    """Citar la ausencia es la cita mas honesta ante un campo ilegible."""
    respuesta = {
        "decision": "request_resubmission",
        "summary": "Hace falta otra foto del reverso para poder opinar.",
        "groundings": [
            {
                "signal_id": "mrz.readable",
                "cited_value": "NO DISPONIBLE",
                "weight": "against",
                "text": "Sin la MRZ no queda nada que verificar con certeza.",
            }
        ],
    }

    run = run_agent(senales(), cliente(tmp_path, json.dumps(respuesta)))

    assert run.outcome is RunOutcome.DECIDED
    assert run.effective_decision is DecisionKind.REQUEST_RESUBMISSION
    assert run.faithful


def test_un_json_que_incumple_el_contrato_no_se_convierte_en_decision(tmp_path):
    """Aprobar citando solo senales en contra es incoherente y lo para el contrato."""
    incoherente = {
        "decision": "approve",
        "summary": "Aprobado pese a que nada lo sostiene.",
        "groundings": [
            {
                "signal_id": "document.expired",
                "cited_value": "False",
                "weight": "against",
                "text": "Un fundamento en contra no puede sostener un aprobado.",
            }
        ],
    }

    run = run_agent(senales(), cliente(tmp_path, json.dumps(incoherente)))

    assert run.outcome is RunOutcome.CONTRACT_VIOLATION
    assert run.decision is None
    assert run.effective_decision is DecisionKind.ESCALATE_TO_HUMAN


def test_una_respuesta_que_no_es_json_no_se_convierte_en_decision(tmp_path):
    run = run_agent(senales(), cliente(tmp_path, "lo siento, no puedo ayudar"))

    assert run.outcome is RunOutcome.MALFORMED
    assert run.effective_decision is DecisionKind.ESCALATE_TO_HUMAN


def test_el_cupo_agotado_se_distingue_del_resto_de_fallos(tmp_path):
    """Una tanda tiene que poder pararse al primer 429 en vez de repetirlo."""
    run = run_agent(
        senales(),
        cliente(tmp_path, '{"error": "quota"}', status=429),
    )

    assert run.outcome is RunOutcome.OUT_OF_QUOTA
    assert run.effective_decision is DecisionKind.ESCALATE_TO_HUMAN


def test_la_api_caida_escala_a_un_humano_en_vez_de_rechazar(tmp_path):
    """Es el punto del diseno que no se puede equivocar.

    Rechazar acusaria a una persona de suplantacion porque se nos cayo un
    proveedor, y aprobar dejaria pasar a cualquiera cuando el proveedor
    falla, que es exactamente el momento que un atacante elegiria.
    """
    run = run_agent(
        senales(),
        cliente(tmp_path, "sobrecargado", status=503, max_retries=0),
    )

    assert run.outcome is RunOutcome.UNAVAILABLE
    assert run.effective_decision is DecisionKind.ESCALATE_TO_HUMAN
    assert run.decision is None


@pytest.mark.parametrize(
    "outcome",
    [o for o in RunOutcome if o is not RunOutcome.DECIDED],
)
def test_ningun_final_sin_decision_aprueba_ni_rechaza(outcome):
    """Barre todos los finales, incluidos los que se anadan despues.

    Escrito como barrido y no como caso a caso a proposito: si manana
    aparece un final nuevo, este test lo cubre sin que nadie se acuerde de
    ampliarlo.
    """
    run = AgentRun(outcome=outcome, signals=senales(), model="gemini-test")

    assert run.effective_decision is FALLBACK_DECISION
    assert run.effective_decision not in (
        DecisionKind.APPROVE,
        DecisionKind.REJECT,
    )
    assert not run.faithful


def test_repetir_la_misma_verificacion_no_gasta_una_segunda_peticion(tmp_path):
    """Es lo que permite reejecutar la evaluacion sin gastar cupo."""
    peticiones: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        cuerpo = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(DECISION_VALIDA)}]}}
            ]
        }
        return httpx.Response(200, json=cuerpo)

    cliente_compartido = GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-test",
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    primera = run_agent(senales(), cliente_compartido)
    segunda = run_agent(senales(), cliente_compartido)

    assert len(peticiones) == 1
    assert not primera.from_cache
    assert segunda.from_cache
    assert segunda.effective_decision is primera.effective_decision
