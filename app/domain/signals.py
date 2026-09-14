"""Catalogo de senales sobre las que el agente razona.

Una senal es un hecho medido sobre la verificacion: la similitud entre la
cara del documento y la de la selfie, la confianza con que el OCR leyo el
nombre, si el numero de cedula tiene un formato valido.

El identificador de cada senal es **estable y publico**: aparece en el
prompt que ve el modelo y reaparece en los fundamentos de su respuesta,
que es lo que permite comprobar despues si cito algo que existe.  Cambiar
un identificador invalida las citas de todas las decisiones guardadas.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum

SignalValue = float | int | str | bool

# Como se nombra la ausencia de una senal, tanto en el prompt que ve el
# agente como en la cita que devuelve.  Es una constante compartida y no
# una cadena suelta en cada sitio porque la comparacion de la cita depende
# de que las dos puntas usen exactamente la misma palabra.
UNAVAILABLE_MARKER = "NO DISPONIBLE"


class SignalKind(str, Enum):
    """Tipo de senal.  Determina como se compara una cita con el valor real."""

    SCORE = "score"  # continuo en 0..1, comparado con tolerancia
    CONFIDENCE = "confidence"  # continuo en 0..1, comparado con tolerancia
    COUNT = "count"  # entero, comparado exacto
    TEXT = "text"  # cadena, comparada normalizada
    FLAG = "flag"  # booleano, comparado exacto


@dataclass(frozen=True)
class Signal:
    id: str
    kind: SignalKind
    description: str
    value: SignalValue | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        # Una senal no disponible con valor seria ambigua: el agente no
        # sabria si puede usarlo.  Mejor que reviente al construirla.
        if self.unavailable_reason is not None and self.value is not None:
            raise ValueError(
                f"la senal {self.id!r} no esta disponible pero trae valor"
            )
        if self.unavailable_reason is None and self.value is None:
            raise ValueError(
                f"la senal {self.id!r} no tiene valor ni motivo de indisponibilidad"
            )

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


class SignalSet:
    """Las senales de una verificacion concreta, indexadas por identificador.

    Incluye tanto las senales que se pudieron calcular como las que no: que
    el OCR fallara es en si mismo informacion que el agente debe ver, y
    ocultarla le llevaria a decidir sin saber que le falta algo.
    """

    def __init__(self, signals: Iterable[Signal]) -> None:
        self._signals: dict[str, Signal] = {}
        for signal in signals:
            if signal.id in self._signals:
                raise ValueError(f"senal duplicada: {signal.id!r}")
            self._signals[signal.id] = signal

    def get(self, signal_id: str) -> Signal | None:
        return self._signals.get(signal_id)

    def __contains__(self, signal_id: object) -> bool:
        return signal_id in self._signals

    def __iter__(self) -> Iterator[Signal]:
        return iter(self._signals.values())

    def __len__(self) -> int:
        return len(self._signals)

    @property
    def available(self) -> list[Signal]:
        return [signal for signal in self if signal.available]
