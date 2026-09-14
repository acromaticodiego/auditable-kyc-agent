"""Catalogo de casos con la decision correcta anotada.

Cada caso construye un anverso y un reverso y declara **que deberia
decidir el sistema y por que**.  Las etiquetas son un juicio mio y estan
escritas para poder discutirse: el campo `reason` existe para que alguien
pueda leer una etiqueta y decir que esta mal.

Tres campos separan cosas que es tentador mezclar:

- `expected_decision` es lo que el sistema deberia decidir **con la
  informacion que tiene**.
- `is_fraud` es la verdad del documento, la sepa el sistema o no.
- `detectable` dice si el fraude deja algun rastro que el sistema pueda
  ver.  Un caso con `is_fraud=True` y `detectable=False` es un documento
  falso que el sistema debe aprobar, porque nada en el permite saberlo.

Esa distincion importa al informar.  Un conjunto donde todos los fraudes
son detectables mide el techo del sistema y lo llama acierto.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from PIL import Image

from app.domain.decision import DecisionKind
from app.evaluation.split import CALIBRATION, check_access, split_of
from app.synthetic.cedula import CedulaData, render_back, render_front
from app.synthetic.degradation import (
    blur,
    crop_edge,
    downscale,
    glare,
    jpeg_artifacts,
    occlude,
    rotate,
)
from app.synthetic.tampering import (
    break_check_digit,
    forge_consistently,
    retouch_front,
    retouch_mrz,
)

TODAY = date(2026, 9, 14)

Pair = tuple[Image.Image, Image.Image]


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    expected_decision: DecisionKind
    reason: str
    build: Callable[[], Pair]
    tags: tuple[str, ...] = ()
    is_fraud: bool = False
    detectable: bool = True
    ambiguous: bool = False

    @property
    def split(self) -> str:
        return split_of(self.id)


def person(**changes) -> CedulaData:
    base = CedulaData(
        nuip="1234567890",
        document_number="000000012",
        surnames="WALTEROS",
        given_names="LAURA",
        birth_date=date(2004, 4, 15),
        birth_place="CARTAGENA (BOLIVAR)",
        sex="F",
        height_m=1.67,
        blood_group="O+",
        issue_date=date(2022, 4, 20),
        issue_place="CARTAGENA",
        expiry_date=date(2032, 4, 19),
    )
    return base.with_changes(**changes) if changes else base


def pair(data: CedulaData, mrz: list[str] | None = None) -> Pair:
    return render_front(data), render_back(data, mrz_lines=mrz)


def _legitimate() -> list[Case]:
    return [
        Case(
            id="legitimo-limpio",
            description="Cedula vigente, captura perfecta.",
            expected_decision=DecisionKind.APPROVE,
            reason="Todo cuadra y la imagen es legible; no hay nada que objetar.",
            build=lambda: pair(person()),
            tags=("legitimo",),
        ),
        Case(
            id="legitimo-torcido",
            description="Cedula vigente fotografiada algo torcida.",
            expected_decision=DecisionKind.APPROVE,
            reason=(
                "Un giro de tres grados no impide leer nada. Rechazar o pedir "
                "otra foto por esto seria perder un cliente legitimo por una "
                "exigencia que el documento no necesita."
            ),
            build=lambda: (rotate(render_front(person()), 3.0),
                           rotate(render_back(person()), 3.0)),
            tags=("legitimo", "captura"),
        ),
        Case(
            id="legitimo-jpeg-moderado",
            description="Cedula vigente reenviada por mensajeria.",
            expected_decision=DecisionKind.APPROVE,
            reason=(
                "La compresion se nota pero el texto sigue siendo legible. Es "
                "la captura tipica de un usuario real y no puede bloquearse."
            ),
            build=lambda: (jpeg_artifacts(render_front(person()), 60),
                           jpeg_artifacts(render_back(person()), 60)),
            tags=("legitimo", "captura"),
        ),
        Case(
            id="legitimo-reflejo-en-zona-vacia",
            description="Reflejo del plastico sobre una zona sin datos.",
            expected_decision=DecisionKind.APPROVE,
            reason=(
                "Hay reflejo y la senal de brillo se dispara, pero cae en el "
                "hueco entre el ultimo campo y la mariposa, sin tapar ningun "
                "dato. Es el caso que distingue una senal de calidad util de "
                "una que solo estorba. "
                "La primera version ponia el reflejo en (0,88 / 0,12) y ahi se "
                "comia el final del NUIP: la etiqueta decia que no tapaba nada "
                "y la imagen decia lo contrario. Lo destapo mirar el panel."
            ),
            build=lambda: (glare(render_front(person()), center=(0.80, 0.72),
                                 radius=0.09),
                           render_back(person())),
            tags=("legitimo", "captura"),
            ambiguous=True,
        ),
    ]


def _tampered() -> list[Case]:
    return [
        Case(
            id="fraude-nuip-retocado-anverso",
            description="El NUIP impreso se cambio y la MRZ quedo como estaba.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "El numero impreso y el que viaja en la MRZ no coinciden. Es "
                "el fraude mas comun: se edita lo que se ve y se olvida el "
                "reverso."
            ),
            build=lambda: pair(*retouch_front(person(), nuip="1098765432")),
            tags=("fraude", "mrz"),
            is_fraud=True,
        ),
        Case(
            id="fraude-fecha-nacimiento-retocada",
            description="La fecha de nacimiento impresa se adelanto seis anos.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "La fecha impresa no coincide con la de la MRZ. Es el retoque "
                "de quien quiere aparentar mayoria de edad."
            ),
            build=lambda: pair(*retouch_front(person(), birth_date=date(1998, 4, 15))),
            tags=("fraude", "mrz"),
            is_fraud=True,
        ),
        Case(
            id="fraude-apellido-retocado",
            description="El apellido impreso se cambio y la MRZ quedo como estaba.",
            expected_decision=DecisionKind.REJECT,
            reason="El apellido impreso no coincide con el de la tercera linea de la MRZ.",
            build=lambda: pair(*retouch_front(person(), surnames="GOMEZ")),
            tags=("fraude", "mrz"),
            is_fraud=True,
        ),
        Case(
            id="fraude-mrz-retocada-expiracion",
            description="La MRZ dice una expiracion distinta de la impresa.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "Caso contrario al anterior: la MRZ es internamente coherente "
                "-- sus digitos cuadran -- pero contradice el anverso. Solo el "
                "cotejo entre las dos copias lo ve."
            ),
            build=lambda: pair(*retouch_mrz(person(), expiry_date=date(2038, 4, 19))),
            tags=("fraude", "mrz"),
            is_fraud=True,
        ),
        Case(
            id="fraude-digito-de-control-roto",
            description="Un digito de control de la MRZ no cuadra.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "Los datos coinciden con el anverso pero la aritmetica de la "
                "MRZ no cierra. Es el descuido de quien edita la MRZ sin saber "
                "que lleva digitos calculados dentro."
            ),
            build=lambda: pair(person(), break_check_digit(person().mrz(), "compuesto")),
            tags=("fraude", "mrz"),
            is_fraud=True,
        ),
        Case(
            id="fraude-coherente-indetectable",
            description="NUIP cambiado y MRZ recalculada entera.",
            expected_decision=DecisionKind.APPROVE,
            reason=(
                "Es un documento falso y la decision correcta sigue siendo "
                "aprobarlo, porque NADA en el permite saberlo: el anverso y la "
                "MRZ concuerdan y los cuatro digitos cuadran. Esta en el "
                "conjunto para medir el techo del sistema en vez de "
                "esconderlo. Un conjunto donde todos los fraudes se detectan "
                "mide ese techo y lo llama acierto."
            ),
            build=lambda: pair(*forge_consistently(person(), nuip="1098765432")),
            tags=("fraude", "indetectable"),
            is_fraud=True,
            detectable=False,
        ),
    ]


def _expired() -> list[Case]:
    return [
        Case(
            id="caducado-legible",
            description="Documento autentico pero vencido hace dos anos.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "No es fraude: es un documento genuino que ya no sirve. La "
                "fecha se lee sin problema, asi que no hay nada que pedir de "
                "nuevo; hay que rechazar y explicar por que."
            ),
            build=lambda: pair(person(expiry_date=date(2024, 4, 19))),
            tags=("caducado",),
        ),
        Case(
            id="caducado-y-borroso",
            description="Documento vencido y ademas mal fotografiado.",
            expected_decision=DecisionKind.REJECT,
            reason=(
                "Discutible a proposito. Se etiqueta como rechazo porque la "
                "fecha de expiracion sigue siendo legible pese al desenfoque, "
                "y pedir otra foto solo retrasaria el mismo rechazo. Si el "
                "desenfoque tapara la fecha, la etiqueta correcta seria pedir "
                "reenvio."
            ),
            build=lambda: (blur(render_front(person(expiry_date=date(2024, 4, 19))), 2.0),
                           blur(render_back(person(expiry_date=date(2024, 4, 19))), 2.0)),
            tags=("caducado", "captura"),
            ambiguous=True,
        ),
    ]


def _bad_capture() -> list[Case]:
    return [
        Case(
            id="captura-borrosa-fuerte",
            description="Foto muy movida.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason=(
                "No se puede leer nada con garantias. Rechazar seria acusar a "
                "la persona de algo cuando el problema es la foto."
            ),
            build=lambda: (blur(render_front(person()), 6.0),
                           blur(render_back(person()), 6.0)),
            tags=("captura",),
        ),
        Case(
            id="captura-reflejo-sobre-el-nuip",
            description="Reflejo del plastico justo encima del NUIP.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason=(
                "El documento puede estar perfectamente bien, pero el campo "
                "que hay que cotejar con la MRZ esta tapado. Sin ese dato no "
                "se puede decidir. "
                "El radio es 0,20 y no 0,16 porque con el reflejo mas pequeno "
                "el numero aun se adivinaba, y un caso etiquetado como "
                "ilegible que en realidad se lee mide lo contrario de lo que "
                "dice medir."
            ),
            build=lambda: (glare(render_front(person()), center=(0.74, 0.16),
                                 radius=0.20),
                           render_back(person())),
            tags=("captura",),
        ),
        Case(
            id="captura-recorte-se-come-la-expiracion",
            description="El encuadre corta la parte inferior del anverso.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason=(
                "La fecha de expiracion no esta borrosa: no esta. No es lo "
                "mismo un campo ilegible que un campo ausente, y los dos "
                "llevan a pedir otra foto por motivos distintos."
            ),
            build=lambda: (crop_edge(render_front(person()), "bottom", 0.22),
                           render_back(person())),
            tags=("captura",),
        ),
        Case(
            id="captura-resolucion-muy-baja",
            description="Foto hecha con una camara mala y muy lejos.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason="La MRZ es lo primero que deja de leerse al bajar la resolucion.",
            build=lambda: (downscale(render_front(person()), 5.0),
                           downscale(render_back(person()), 5.0)),
            tags=("captura",),
        ),
        Case(
            id="captura-dedo-sobre-la-fecha",
            description="Algo tapa la fecha de nacimiento.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason=(
                "Un solo campo tapado, el resto perfecto. El sistema deberia "
                "pedir otra foto y no inventarse el dato que falta ni "
                "decidir sin el."
            ),
            build=lambda: (occlude(render_front(person()), (0.32, 0.50, 0.56, 0.62)),
                           render_back(person())),
            tags=("captura",),
        ),
        Case(
            id="captura-reverso-ilegible",
            description="Anverso perfecto, reverso irreconocible.",
            expected_decision=DecisionKind.REQUEST_RESUBMISSION,
            reason=(
                "Sin MRZ legible se pierde la unica senal con certeza del "
                "documento. Decidir solo con el anverso es posible, pero "
                "renuncia justo a la comprobacion que mas pesa."
            ),
            build=lambda: (render_front(person()),
                           blur(downscale(render_back(person()), 6.0), 3.0)),
            tags=("captura", "mrz"),
            ambiguous=True,
        ),
    ]


def build_catalog() -> list[Case]:
    cases = _legitimate() + _tampered() + _expired() + _bad_capture()

    identifiers = [case.id for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("hay identificadores de caso repetidos")

    return cases


def load_cases(
    split: str = CALIBRATION, *, final_measurement: bool = False
) -> list[Case]:
    """Los casos de una mitad.

    Pedir el reservado sin declarar que es la medicion final lanza
    `HoldoutLocked`; ver app/evaluation/split.py.
    """
    check_access(split, final_measurement=final_measurement)
    return [case for case in build_catalog() if case.split == split]
