"""Linea base: decidir con reglas fijas, sin modelo de lenguaje.

Existe para responder la pregunta incomoda del proyecto: **¿cuanto aporta
el agente de verdad?** Sin esta comparacion, cualquier tasa de acierto del
agente se lee como merito suyo cuando podria ser merito de que las senales
deterministas ya resolvian el caso.

Las reglas estan escritas para ser lo mejor que se puede hacer sin razonar,
no para ser un espantapajaros facil de vencer. Una linea base debil hace
quedar bien al agente y no informa de nada.

Los umbrales se eligieron mirando **solo la mitad de calibracion** del
conjunto. Su acierto sobre esa mitad no es una medida: es el resultado de
ajustar sobre los datos que se estan midiendo, y saldra alto por
construccion. La medida es lo que haga sobre el reservado.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.decision import AgentDecision, DecisionKind, Grounding, Weight
from app.domain.signals import SignalSet
from app.signals.pipeline import contradictions


@dataclass(frozen=True)
class Thresholds:
    """Cortes del arbol de decision.

    Elegidos sobre la mitad de calibracion (9 casos), lo que es una muestra
    diminuta: cada umbral se apoya en dos o tres puntos. Se dejan como
    parametros y no como constantes dentro del codigo para que se vea que
    son una eleccion y no una ley.
    """

    # Entre 1505 (la captura legible mas floja) y 10 (la ilegible). El hueco
    # es enorme y el corte esta a ojo en medio: con dos puntos no se puede
    # hacer nada mejor.
    min_sharpness: int = 300
    # Entre 0.645 (un reflejo que no tapa nada) y 0.954 (uno que tapa el
    # NUIP). Dos puntos, un corte.
    max_glare: float = 0.80
    # Basta con que falte un campo para no acusar a nadie: si parte de la
    # evidencia no esta, una contradiccion puede venir de que la lectura se
    # haya desplazado.
    max_missing_fields: int = 0


def _value(signals: SignalSet, signal_id: str):
    signal = signals.get(signal_id)
    return signal.value if signal and signal.available else None


def _ground(signals: SignalSet, signal_id: str, weight: Weight, text: str) -> Grounding:
    """Fundamento que cita la senal igual que lo haria el agente.

    La linea base tambien cita, y por el mismo motivo: asi su explicacion
    pasa por el mismo verificador y la comparacion entre las dos es justa.
    """
    signal = signals.get(signal_id)
    cited = (
        str(signal.value) if signal and signal.available else "NO DISPONIBLE"
    )
    return Grounding(
        signal_id=signal_id, cited_value=cited, weight=weight, text=text
    )


def decide(signals: SignalSet, thresholds: Thresholds | None = None) -> AgentDecision:
    limits = thresholds or Thresholds()

    readable = _value(signals, "mrz.readable")
    sharpness = _value(signals, "quality.front_sharpness")
    glare = _value(signals, "quality.front_glare")
    missing = _value(signals, "ocr.fields_missing")
    checks_ok = _value(signals, "mrz.checks_ok")
    expired = _value(signals, "document.expired")
    disagreements = contradictions(signals)

    # 1. Sin MRZ no hay nada con certeza que comprobar.
    if readable is False:
        return AgentDecision(
            decision=DecisionKind.REQUEST_RESUBMISSION,
            groundings=[
                _ground(
                    signals, "mrz.readable", Weight.AGAINST,
                    "No se pudo leer la MRZ del reverso, que es la unica parte "
                    "del documento que se puede verificar con certeza.",
                )
            ],
            summary="Hace falta otra foto del reverso: la MRZ no es legible.",
        )

    # 2 y 3. Problemas de captura que impiden fiarse de lo leido.
    if sharpness is not None and sharpness < limits.min_sharpness:
        return AgentDecision(
            decision=DecisionKind.REQUEST_RESUBMISSION,
            groundings=[
                _ground(
                    signals, "quality.front_sharpness", Weight.AGAINST,
                    f"La nitidez del anverso ({sharpness}) esta por debajo del "
                    f"minimo de {limits.min_sharpness} para leerlo con garantias.",
                )
            ],
            summary="Hace falta otra foto: el anverso esta demasiado movido.",
        )

    if glare is not None and glare > limits.max_glare:
        return AgentDecision(
            decision=DecisionKind.REQUEST_RESUBMISSION,
            groundings=[
                _ground(
                    signals, "quality.front_glare", Weight.AGAINST,
                    f"Hay un reflejo localizado ({glare}) que supera el maximo "
                    f"de {limits.max_glare} y puede estar tapando un campo.",
                )
            ],
            summary="Hace falta otra foto: un reflejo tapa parte del anverso.",
        )

    # 4. Falta evidencia: no se acusa a nadie con los datos incompletos.
    if missing is not None and missing > limits.max_missing_fields:
        return AgentDecision(
            decision=DecisionKind.REQUEST_RESUBMISSION,
            groundings=[
                _ground(
                    signals, "ocr.fields_missing", Weight.AGAINST,
                    f"No se localizaron {missing} de los campos clave del "
                    "anverso, asi que parte de la evidencia no esta.",
                )
            ],
            summary="Hace falta otra foto: faltan campos por leer en el anverso.",
        )

    # 5. La aritmetica de la MRZ no cierra.
    if checks_ok is False:
        return AgentDecision(
            decision=DecisionKind.REJECT,
            groundings=[
                _ground(
                    signals, "mrz.checks_ok", Weight.AGAINST,
                    "Los digitos de control de la MRZ no cuadran pese a que la "
                    "imagen es legible, asi que el documento no es coherente "
                    "consigo mismo.",
                )
            ],
            summary="Se rechaza: la MRZ del documento no cuadra.",
        )

    # 6. Las dos copias del mismo dato no coinciden.
    if disagreements:
        return AgentDecision(
            decision=DecisionKind.REJECT,
            groundings=[
                _ground(
                    signals, f"cross.{field}", Weight.AGAINST,
                    f"El campo {field} impreso en el anverso no coincide con el "
                    "que viaja en la MRZ, y la imagen es legible.",
                )
                for field in disagreements
            ],
            summary="Se rechaza: el anverso contradice a la MRZ.",
        )

    # 7. Documento autentico pero vencido.
    if expired is True:
        return AgentDecision(
            decision=DecisionKind.REJECT,
            groundings=[
                _ground(
                    signals, "document.expired", Weight.AGAINST,
                    "El documento esta vencido.",
                )
            ],
            summary="Se rechaza: el documento esta vencido.",
        )

    return AgentDecision(
        decision=DecisionKind.APPROVE,
        groundings=[
            _ground(
                signals, "mrz.checks_ok", Weight.IN_FAVOR,
                "Los cuatro digitos de control de la MRZ cuadran.",
            ),
            _ground(
                signals, "cross.nuip", Weight.IN_FAVOR,
                "El numero de identidad impreso coincide con el de la MRZ.",
            ),
            _ground(
                signals, "document.expired", Weight.IN_FAVOR,
                "El documento no esta vencido.",
            ),
        ],
        summary="Se aprueba: todo cuadra y la imagen es legible.",
    )
