"""Verificacion de las citas del agente contra las senales reales.

Responde a la primera pregunta que hace cualquiera en banca ante un
sistema asi: *y si el modelo se inventa la razon*.  Cada fundamento de la
decision se contrasta con el valor que la senal tenia de verdad, y el
resultado es un numero que se puede ensenar en vez de una promesa.

Todo lo que hay aqui es determinista y no gasta cupo de la API: se puede
ejecutar sobre decisiones ya guardadas tantas veces como haga falta.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from app.domain.decision import AgentDecision
from app.domain.signals import Signal, SignalKind, SignalSet, SignalValue

# Tolerancia para senales continuas.
#
# El modelo cita los scores redondeados a dos decimales, y algunos modelos
# truncan en vez de redondear: 0.6178 puede aparecer como "0.61", una
# diferencia de hasta 0.0099.  La tolerancia cubre ese caso y poco mas.
#
# El valor no es inocente: subirlo infla la metrica de fidelidad hasta
# volverla trivial.  Con 0.011 sobre una escala 0..1, un modelo que
# atribuya 0.85 a una senal que valia 0.61 sigue fallando sin discusion.
SCORE_TOLERANCE = 0.011

# Margen para el error de representacion en coma flotante.  Sin el, la
# frontera es impredecible: 0.700 - 0.689 da 0.011000000000000010 en float y
# quedaba fuera de una tolerancia de 0.011 por un error de representacion,
# no por una discrepancia real.  Lo encontro el test de frontera.
_FLOAT_EPSILON = 1e-9


class CitationStatus(str, Enum):
    VALID = "valid"
    # El identificador citado no existe en el catalogo: invencion pura.
    UNKNOWN_SIGNAL = "unknown_signal"
    # La senal existe pero no se pudo calcular; atribuirle un valor es
    # inventarse una medicion que nunca se hizo.
    UNAVAILABLE_SIGNAL = "unavailable_signal"
    # La senal existe y tiene valor, pero el citado no es ese.
    VALUE_MISMATCH = "value_mismatch"
    # Se cito la senal sin decir cuanto valia.  Cuenta como invalida a
    # proposito: si no citar valor saliera gratis, el camino mas seguro
    # para el modelo seria no citar ninguno y la metrica no mediria nada.
    MISSING_CITED_VALUE = "missing_cited_value"


@dataclass(frozen=True)
class CitationResult:
    signal_id: str
    status: CitationStatus
    cited_value: SignalValue | None
    actual_value: SignalValue | None

    @property
    def valid(self) -> bool:
        return self.status is CitationStatus.VALID


@dataclass(frozen=True)
class AuditReport:
    results: list[CitationResult]

    @property
    def invalid(self) -> list[CitationResult]:
        return [result for result in self.results if not result.valid]

    @property
    def faithful(self) -> bool:
        """La decision es fiel si ninguna de sus citas es invalida."""
        return not self.invalid


def _normalize_text(value: object) -> str:
    """Minusculas, sin acentos y con los espacios colapsados.

    Se compara asi porque lo que se audita es si el modelo repite el valor
    que se le dio, no si sabe escribirlo con tildes.  'JUAN DIEGO OSSA' y
    'Juan Diego Ossa' son la misma cita; 'Juan Diego Ossa' y 'Juan Ossa'
    no lo son.
    """
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().split())


def _values_match(signal: Signal, cited: SignalValue) -> bool:
    if signal.kind in (SignalKind.SCORE, SignalKind.CONFIDENCE):
        try:
            difference = abs(float(cited) - float(signal.value))
            return difference <= SCORE_TOLERANCE + _FLOAT_EPSILON
        except (TypeError, ValueError):
            return False

    if signal.kind is SignalKind.COUNT:
        try:
            return int(cited) == int(signal.value)
        except (TypeError, ValueError):
            return False

    if signal.kind is SignalKind.FLAG:
        return isinstance(cited, bool) and cited is signal.value

    return _normalize_text(cited) == _normalize_text(signal.value)


def audit_citations(decision: AgentDecision, signals: SignalSet) -> AuditReport:
    """Contrasta cada fundamento de la decision con la senal que dice citar."""
    results: list[CitationResult] = []

    for grounding in decision.groundings:
        signal = signals.get(grounding.signal_id)

        if signal is None:
            status = CitationStatus.UNKNOWN_SIGNAL
        elif not signal.available:
            status = CitationStatus.UNAVAILABLE_SIGNAL
        elif grounding.cited_value is None:
            status = CitationStatus.MISSING_CITED_VALUE
        elif _values_match(signal, grounding.cited_value):
            status = CitationStatus.VALID
        else:
            status = CitationStatus.VALUE_MISMATCH

        results.append(
            CitationResult(
                signal_id=grounding.signal_id,
                status=status,
                cited_value=grounding.cited_value,
                actual_value=signal.value if signal is not None else None,
            )
        )

    return AuditReport(results=results)
