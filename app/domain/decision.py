"""Contrato de la decision del agente.

El agente no devuelve un parrafo.  Devuelve una decision y una lista de
fundamentos, y cada fundamento **cita el identificador de una senal y el
valor que le atribuye**.  Esa obligacion es lo que hace la explicacion
auditable: sin el valor citado no hay nada que contrastar, y la
explicacion vuelve a ser prosa que hay que creerse.

Ver docs/adr/0002-explicacion-auditable.md.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class DecisionKind(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    # Distinta de rechazar a proposito: "esta foto no me deja opinar" no es
    # lo mismo que "esta persona no es quien dice ser".  Confundirlas hace
    # perder clientes legitimos por una foto movida.
    REQUEST_RESUBMISSION = "request_resubmission"


class Weight(str, Enum):
    IN_FAVOR = "in_favor"
    AGAINST = "against"
    INCONCLUSIVE = "inconclusive"


class Grounding(BaseModel):
    """Un fundamento: una senal, el valor que el agente le atribuye y por que pesa."""

    signal_id: str = Field(min_length=1)
    cited_value: float | int | str | bool | None = None
    weight: Weight
    text: str = Field(min_length=10)


class AgentDecision(BaseModel):
    decision: DecisionKind
    # Al menos un fundamento.  Sin este minimo, un modelo podria sacar
    # fidelidad de citas perfecta simplemente no citando nada, que es
    # exactamente el incentivo que no queremos crear.
    groundings: list[Grounding] = Field(min_length=1)
    summary: str = Field(min_length=10)

    @model_validator(mode="after")
    def _grounding_must_support_the_decision(self) -> AgentDecision:
        """Cada decision necesita al menos un fundamento que apunte a ella.

        Aprobar citando unicamente senales en contra es incoherente, y lo
        mismo al reves.  No se prohibe que existan fundamentos en la
        direccion contraria: decidir a pesar de una senal adversa es
        legitimo y es justo lo que se quiere poder leer despues.
        """
        weights = {grounding.weight for grounding in self.groundings}

        if self.decision is DecisionKind.APPROVE:
            if Weight.IN_FAVOR not in weights:
                raise ValueError(
                    "aprobar exige al menos un fundamento a favor"
                )
        elif weights == {Weight.IN_FAVOR}:
            raise ValueError(
                f"{self.decision.value} exige al menos un fundamento en contra "
                "o no concluyente"
            )
        return self
