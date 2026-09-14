"""De dos imagenes a las senales sobre las que razona el agente.

Todo lo que hay aqui es determinista y se calcula **siempre**, antes de
llamar a ningun modelo (ADR-0001). Ninguna de estas funciones cuesta cupo
de API y todas se pueden reejecutar sobre las mismas imagenes tantas veces
como haga falta.

El criterio para incluir una senal no es lo interesante que suene sino si
el agente puede hacer algo con ella. Cada una lleva una descripcion que se
le muestra tal cual, porque el identificador por si solo no dice si 2033
es mucho o poco.
"""

from __future__ import annotations

from datetime import date

from PIL import Image

from app.signals.cross_check import CrossStatus, cross_check, is_expired
from app.signals.mrz import MrzData
from app.signals.ocr_front import FrontFields, read_front
from app.signals.ocr_mrz import MrzReading, read_mrz
from app.signals.quality import glare_contrast, sharpness
from app.domain.signals import Signal, SignalKind, SignalSet

# Campos del anverso que se publican como senal con su confianza.
PUBLISHED_FIELDS = {
    "nuip": "Numero unico de identificacion leido del anverso.",
    "surnames": "Apellidos leidos del anverso.",
    "given_names": "Nombres leidos del anverso.",
    "birth_date": "Fecha de nacimiento leida del anverso.",
    "expiry_date": "Fecha de expiracion leida del anverso.",
}

CROSS_DESCRIPTIONS = {
    "nuip": "numero de identidad",
    "birth_date": "fecha de nacimiento",
    "expiry_date": "fecha de expiracion",
    "surnames": "apellidos",
    "given_names": "nombres",
    "sex": "sexo",
}


def _quality_signals(front: Image.Image, back: Image.Image) -> list[Signal]:
    return [
        Signal(
            "quality.front_sharpness",
            SignalKind.COUNT,
            "Nitidez del anverso (varianza del laplaciano). Una captura "
            "limpia de este documento ronda 2000; por debajo de 50 el texto "
            "pequeno deja de leerse. El valor absoluto solo es comparable "
            "entre capturas del mismo tipo de documento.",
            value=round(sharpness(front)),
        ),
        Signal(
            "quality.back_sharpness",
            SignalKind.COUNT,
            "Nitidez del reverso, donde vive la MRZ. Misma escala que la "
            "del anverso.",
            value=round(sharpness(back)),
        ),
        Signal(
            "quality.front_glare",
            SignalKind.SCORE,
            "Cuanto destaca la zona mas brillante del anverso sobre el resto "
            "(0 a 1). Detecta el reflejo del policarbonato, que puede tapar "
            "un campo sin que la imagen este movida. Por encima de 0.5 hay "
            "un brillo localizado; por debajo de 0.3 no lo hay.",
            value=round(glare_contrast(front), 3),
        ),
    ]


def _mrz_signals(reading: MrzReading, mrz: MrzData | None) -> list[Signal]:
    if mrz is None:
        motivo = reading.error or "la MRZ no se pudo interpretar"
        return [
            Signal(
                "mrz.readable",
                SignalKind.FLAG,
                "Si se pudo leer la MRZ del reverso.",
                value=False,
            ),
            Signal(
                "mrz.checks_ok",
                SignalKind.FLAG,
                "Si los cuatro digitos de control de la MRZ cuadran.",
                unavailable_reason=motivo,
            ),
            Signal(
                "mrz.failed_checks",
                SignalKind.TEXT,
                "Que digitos de control de la MRZ no cuadran.",
                unavailable_reason=motivo,
            ),
        ]

    return [
        Signal(
            "mrz.readable",
            SignalKind.FLAG,
            "Si se pudo leer la MRZ del reverso.",
            value=True,
        ),
        Signal(
            "mrz.checks_ok",
            SignalKind.FLAG,
            "Si los cuatro digitos de control de la MRZ cuadran. Son "
            "aritmetica pura: si no cuadran, o el documento se manipulo o el "
            "OCR leyo mal. Que cuadren NO prueba que el documento sea "
            "autentico, solo que es coherente consigo mismo.",
            value=mrz.checks_ok,
        ),
        Signal(
            "mrz.failed_checks",
            SignalKind.TEXT,
            "Que digitos de control no cuadran, si alguno.",
            value=", ".join(mrz.failed_checks) if mrz.failed_checks else "ninguno",
        ),
        Signal(
            "mrz.repaired",
            SignalKind.FLAG,
            "Si hubo que corregir un caracter de la lectura para que los "
            "digitos cuadraran. Indica una imagen justa de calidad, no "
            "manipulacion.",
            value=reading.repaired,
        ),
    ]


def _front_signals(front_fields: FrontFields) -> list[Signal]:
    signals: list[Signal] = []
    for name, description in PUBLISHED_FIELDS.items():
        field = front_fields.get(name)
        if field is None:
            signals.append(
                Signal(
                    f"ocr.{name}",
                    SignalKind.TEXT,
                    description,
                    unavailable_reason="el campo no se localizo en el anverso",
                )
            )
            signals.append(
                Signal(
                    f"ocr.{name}_confidence",
                    SignalKind.CONFIDENCE,
                    f"Confianza del OCR al leer {name} (0 a 1).",
                    unavailable_reason="el campo no se localizo en el anverso",
                )
            )
            continue

        origen = (
            "leido debajo de su rotulo impreso"
            if field.source == "label"
            else "DEDUCIDO por su posicion, porque el rotulo no se pudo leer; "
            "menos fiable que un campo localizado por su rotulo"
        )
        signals.append(
            Signal(
                f"ocr.{name}", SignalKind.TEXT, f"{description} Origen: {origen}.",
                value=field.value,
            )
        )
        signals.append(
            Signal(
                f"ocr.{name}_confidence",
                SignalKind.CONFIDENCE,
                f"Confianza del OCR al leer {name} (0 a 1). En las medidas "
                "sobre documentos sinteticos, las lecturas correctas "
                "promediaron 0.89 y las erroneas 0.56.",
                value=round(field.confidence, 2),
            )
        )
    return signals


def _cross_signals(front_fields: FrontFields, mrz: MrzData | None) -> list[Signal]:
    signals: list[Signal] = []
    for result in cross_check(front_fields, mrz):
        etiqueta = CROSS_DESCRIPTIONS[result.field]
        detalle = (
            f"Impreso en el anverso: {result.front_value!r}. "
            f"En la MRZ: {result.mrz_value!r}."
        )
        signals.append(
            Signal(
                f"cross.{result.field}",
                SignalKind.TEXT,
                f"Cotejo del {etiqueta} entre el anverso y la MRZ. "
                f"'match' si coinciden, 'mismatch' si se contradicen, y "
                f"'*_missing' si una de las dos copias no se pudo leer. "
                f"Un 'mismatch' puede ser manipulacion del documento o un "
                f"fallo del OCR: hay que mirar la confianza de la lectura. "
                f"{detalle}",
                value=result.status.value,
            )
        )
    return signals


def build_signals(
    front: Image.Image, back: Image.Image, today: date | None = None
) -> SignalSet:
    today = today or date.today()

    front_fields = read_front(front)
    reading = read_mrz(back)
    mrz = reading.parsed()

    signals: list[Signal] = []
    signals += _quality_signals(front, back)
    signals += _mrz_signals(reading, mrz)
    signals += _front_signals(front_fields)
    signals += _cross_signals(front_fields, mrz)

    expired = is_expired(front_fields, mrz, today)
    signals.append(
        Signal(
            "document.expired",
            SignalKind.FLAG,
            f"Si el documento esta vencido a fecha de {today.isoformat()}. "
            "Se prefiere la fecha de la MRZ porque lleva un digito de control "
            "detras; la del anverso es el respaldo.",
            value=expired,
        )
        if expired is not None
        else Signal(
            "document.expired",
            SignalKind.FLAG,
            "Si el documento esta vencido.",
            unavailable_reason="no se pudo leer ninguna fecha de expiracion",
        )
    )

    return SignalSet(signals)


def contradictions(signals: SignalSet) -> list[str]:
    """Los campos en los que el anverso y la MRZ se contradicen.

    Es un atajo para la linea base de reglas y para las medidas; el agente
    recibe las senales de cotejo una a una y decide por su cuenta.
    """
    return [
        signal.id.removeprefix("cross.")
        for signal in signals
        if signal.id.startswith("cross.")
        and signal.available
        and signal.value == CrossStatus.MISMATCH.value
    ]
