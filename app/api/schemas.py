"""El contrato publico de la API, escrito para que se lea en /docs.

Antes las tres rutas devolvian `dict` y el esquema publicado era
`{"type": "object", "additionalProperties": true}`: literalmente "aqui va
JSON, suerte".  Quien abriera /docs no podia saber que decisiones existen,
que veredictos emite el auditor ni que significa cada campo, y tenia que
leerse el codigo para integrarse.

Los enumerados se toman de las clases del dominio en vez de repetirse a
mano.  Es lo que hace que /docs muestre las cuatro decisiones posibles y
los cinco veredictos de cita, y que una decision nueva en el dominio
aparezca aqui sin que nadie se acuerde de copiarla.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent.runner import RunOutcome
from app.domain.citation_audit import CitationStatus
from app.domain.decision import DecisionKind, Weight


class FundamentoAuditado(BaseModel):
    """Un motivo de la decision, con el veredicto de su comprobacion al lado."""

    signal_id: str = Field(description="La senal que el agente dice citar.")
    valor_citado: str | None = Field(
        description="El valor que el agente le atribuyo a esa senal."
    )
    peso: Weight = Field(description="Si juega a favor, en contra o no concluye.")
    texto: str = Field(description="Por que esa senal pesa en la decision.")
    auditoria: CitationStatus = Field(
        description=(
            "Resultado de contrastar la cita con el valor real de la senal. "
            "Cualquier valor distinto de 'valid' significa que esa parte de "
            "la explicacion no se sostiene."
        )
    )


class VerificacionCreada(BaseModel):
    """Lo que devuelve una verificacion recien hecha."""

    id: uuid.UUID
    decision: DecisionKind = Field(
        description=(
            "Lo que el sistema hace con la solicitud. Ante un fallo del "
            "modelo es 'escalate_to_human'; ver docs/adr/0004."
        )
    )
    resultado_del_agente: RunOutcome = Field(
        description=(
            "Como acabo la vuelta del agente. Distinto de la decision: un "
            "'unavailable' tambien produce una decision, la de escalar."
        )
    )
    resumen: str | None = Field(
        description="Resumen del agente, o nulo si no llego a decidir."
    )
    explicacion_fiel: bool = Field(
        description=(
            "Ninguna cita es falsa. NO significa que la explicacion este "
            "completa: mide que no mienta, no que lo cuente todo."
        )
    )
    explicacion_completa: bool = Field(
        description=(
            "La explicacion menciona todas las senales que jugaban en "
            "contra. Una explicacion puede ser fiel y no ser completa."
        )
    )
    senales_adversas_omitidas: list[str] = Field(
        description="Las senales en contra que la explicacion no menciona."
    )
    fundamentos: list[FundamentoAuditado]
    error: str | None = Field(
        description="Por que no hubo decision, si no la hubo."
    )


class SenalRegistrada(BaseModel):
    """Una senal tal y como se midio, guardada para poder auditar despues."""

    signal_id: str
    tipo: str
    valor: str | None = Field(
        description=(
            "El valor como lo vio el agente, en texto. Nulo si no se pudo "
            "medir."
        )
    )
    disponible: bool
    motivo_indisponible: str | None
    adversa: bool = Field(
        description=(
            "Si esta senal, con este valor, jugaba en contra de la "
            "solicitud. Se marca aunque el agente no llegara a decidir."
        )
    )
    omitida: bool = Field(
        description="Si jugaba en contra y la explicacion no la menciono."
    )


class FundamentoRegistrado(FundamentoAuditado):
    orden: int = Field(
        description=(
            "El orden en que el agente dio los fundamentos. El primero suele "
            "ser el que de verdad sostiene la decision."
        )
    )


class VerificacionRegistrada(BaseModel):
    """El expediente completo: la decision y la evidencia que tenia delante.

    Lleva las senales medidas y no solo la decision porque es lo que hace
    auditable el registro: sin los valores que el agente tenia delante, sus
    fundamentos son afirmaciones que hay que creerse.
    """

    id: uuid.UUID
    creado_en: datetime
    modelo: str = Field(description="Que modelo decidio.")
    resultado: RunOutcome
    decision: DecisionKind
    resumen: str | None
    error: str | None
    desde_cache: bool
    anverso_sha256: str = Field(
        description=(
            "Hash de la imagen. Las imagenes NO se guardan: un registro de "
            "auditoria no puede convertirse en un almacen de cedulas."
        )
    )
    reverso_sha256: str
    selfie_sha256: str | None = Field(
        default=None,
        description="Nulo si no se aporto selfie, que es un caso legitimo.",
    )
    citas_validas: int
    citas_totales: int
    explicacion_fiel: bool
    explicacion_completa: bool
    senales: list[SenalRegistrada]
    fundamentos: list[FundamentoRegistrado]


class SenalCallada(BaseModel):
    signal_id: str
    veces: int


class ResumenAuditoria(BaseModel):
    """Las cuentas del conjunto. Recuentos, nunca porcentajes.

    Sobre cinco verificaciones un porcentaje es una cifra con aspecto de
    medida que no mide nada, y quien la lea de reojo la tratara como si
    midiera. El denominador va delante para que no se pueda leer el
    numerador sin el.
    """

    verificaciones: int
    por_decision: dict[str, int]
    por_resultado_del_agente: dict[str, int]
    escalados_sin_juicio_del_agente: int = Field(
        description=(
            "Escalados que no salen de un juicio del agente sino de que no "
            "llego a haber juicio. Es el numero incomodo del ADR-0004: un "
            "agente que incumple el contrato a menudo manda a un analista "
            "solicitudes que no lo necesitaban."
        )
    )
    con_explicacion: int = Field(
        description=(
            "Denominador de las dos cifras siguientes. Solo las "
            "verificaciones en las que el agente llego a explicarse."
        )
    )
    explicaciones_fieles: int
    explicaciones_completas: int
    senales_adversas_mas_calladas: list[SenalCallada]
    advertencia: str
