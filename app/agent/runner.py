"""El bucle que convierte un juego de senales en una decision del agente.

Une las piezas que ya existian sueltas: `build_prompt` arma el listado de
senales, `GeminiClient` lo lleva al modelo con el esquema impuesto,
`AgentDecision` valida que lo devuelto cumpla el contrato y
`audit_citations` comprueba, una a una, que las citas digan la verdad.

Tres decisiones de diseno que no son obvias:

**No se reintenta cuando el modelo incumple el contrato.**  Es tentador
devolverle el error de validacion y pedirle que lo corrija, y en otro
proyecto seria lo correcto.  Aqui no, por dos motivos.  El primero es el
cupo: reintentar con un mensaje correctivo es una peticion nueva sobre un
tope de 20 al dia, y repetir el mismo prompt no sirve porque la respuesta
mala ya esta en cache y volveria identica.  El segundo pesa mas: reparar
en silencio esconde cada cuanto el modelo rompe el contrato, y esa
frecuencia es justo uno de los numeros que hay que publicar.

**Cuando no hay decision, el sistema escala a un humano.**  Ni aprueba ni
rechaza.  Aprobar sin haber leido la evidencia es el fallo caro de un KYC,
y rechazar acusaria a una persona de suplantacion porque se nos cayo la
API.  Escalar es admitir que el sistema no sabe, que es lo unico cierto en
ese momento.

**La auditoria viaja con la decision pero todavia no la corrige.**  Seria
defendible degradar a `escalate_to_human` toda decision con citas falsas.
Sin haber medido cuantas hay ni de que tipo, esa regla seria un corte
elegido a ojo, y este proyecto no elige cortes a ojo.  Queda como pregunta
abierta hasta tener el numero sobre calibracion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from app.agent.budget import BudgetExhausted
from app.agent.gemini import (
    GeminiClient,
    GeminiError,
    QuotaExhausted,
    parse_json_response,
)
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.domain.citation_audit import AuditReport, audit_citations
from app.domain.completeness import CompletenessReport, audit_completeness
from app.domain.decision import AgentDecision, DecisionKind
from app.domain.signals import SignalSet

# A donde va una solicitud cuando el agente no consigue decidir.  Ver el
# segundo parrafo de la cabecera: no es un valor por defecto cualquiera,
# es la unica salida que no miente sobre lo que el sistema sabe.
FALLBACK_DECISION = DecisionKind.ESCALATE_TO_HUMAN


class RunOutcome(str, Enum):
    """Como acabo la vuelta del agente.

    Se distinguen cuatro finales en vez de un booleano porque la reaccion
    a cada uno es distinta, y porque mezclarlos borraria la diferencia
    entre "el modelo razona mal" y "el modelo no contesto", que es la
    diferencia que hay que poder informar.
    """

    DECIDED = "decided"
    # JSON valido pero incumple el contrato: cita sin valor, no fundamenta
    # la decision, decision fuera del enumerado.
    CONTRACT_VIOLATION = "contract_violation"
    # Ni siquiera es JSON, pese al esquema impuesto.
    MALFORMED = "malformed"
    # No se llego a obtener respuesta: red, corte por tiempo, filtro de
    # seguridad, 5xx que no cedio al reintento.
    UNAVAILABLE = "unavailable"
    # Se acabo el cupo.  Separado de UNAVAILABLE porque una tanda debe
    # PARARSE aqui: seguir con los casos que faltan solo gastaria tiempo
    # produciendo el mismo error veinte veces.
    OUT_OF_QUOTA = "out_of_quota"


@dataclass(frozen=True)
class AgentRun:
    outcome: RunOutcome
    signals: SignalSet
    model: str
    decision: AgentDecision | None = None
    audit: AuditReport | None = None
    completeness: CompletenessReport | None = None
    from_cache: bool = False
    error: str | None = None

    @property
    def effective_decision(self) -> DecisionKind:
        """Lo que el sistema hace de verdad con esta solicitud.

        Distinta de `decision.decision` a proposito: una tanda de
        evaluacion tiene que puntuar tambien los casos en los que el agente
        no contesto, y puntuarlos por lo que el sistema acaba haciendo.
        Saltarselos convertiria cada fallo del modelo en un caso que no
        cuenta, e inflaria el acierto justo donde peor se comporta.
        """
        if self.decision is None:
            return FALLBACK_DECISION
        return self.decision.decision

    @property
    def complete(self) -> bool:
        """Si la explicacion menciona todas las senales que jugaban en contra.

        Distinta de `faithful` a proposito: una explicacion puede ser
        verdadera entera y aun asi callarse lo unico que importaba. Ver
        app/domain/completeness.py.
        """
        return self.completeness is not None and self.completeness.complete

    @property
    def faithful(self) -> bool:
        """Si ninguna cita de la decision es falsa.

        Una vuelta sin decision no es fiel ni infiel: no dijo nada que
        contrastar.  Se cuenta como no fiel para que la metrica no premie
        al modelo que calla, por el mismo motivo que `AgentDecision` exige
        al menos un fundamento.
        """
        return self.audit is not None and self.audit.faithful


def run_agent(signals: SignalSet, client: GeminiClient) -> AgentRun:
    """Una vuelta: senales, prompt, modelo, contrato y auditoria.

    No lanza excepciones por un fallo del modelo o de la API: los devuelve
    como `outcome`.  Una tanda de 12 casos no puede abortar entera porque
    el septimo devolviera un JSON torcido.
    """
    prompt = build_prompt(signals)

    def failed(outcome: RunOutcome, error: str, from_cache: bool = False) -> AgentRun:
        return AgentRun(
            outcome=outcome,
            signals=signals,
            model=client.model,
            from_cache=from_cache,
            error=error,
        )

    try:
        response = client.generate_json(prompt, DECISION_RESPONSE_SCHEMA)
    except (QuotaExhausted, BudgetExhausted) as error:
        return failed(RunOutcome.OUT_OF_QUOTA, str(error))
    except GeminiError as error:
        return failed(RunOutcome.UNAVAILABLE, str(error))

    try:
        payload = parse_json_response(response.text)
    except GeminiError as error:
        return failed(RunOutcome.MALFORMED, str(error), response.from_cache)

    try:
        decision = AgentDecision.model_validate(payload)
    except ValidationError as error:
        return failed(RunOutcome.CONTRACT_VIOLATION, str(error), response.from_cache)

    return AgentRun(
        outcome=RunOutcome.DECIDED,
        signals=signals,
        model=response.model,
        decision=decision,
        audit=audit_citations(decision, signals),
        completeness=audit_completeness(decision, signals),
        from_cache=response.from_cache,
    )
