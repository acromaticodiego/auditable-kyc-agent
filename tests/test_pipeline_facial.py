"""Pruebas de la senal facial dentro del pipeline, con el lector simulado.

El lector real necesita caras, y las caras reales viven fuera del
repositorio. Lo que se fija aqui es la logica que rodea al modelo: que el
motivo de ausencia diga en cual de las dos imagenes esta el problema, y que
no aparezca nunca un valor inventado.

Esa distincion no es cosmetica. Que falte la selfie es un problema de la
solicitud, que no haya cara en el anverso apunta a una foto mala del
documento, y que no la haya en la selfie se arregla pidiendo otra. Las tres
llevan al agente a decisiones distintas.
"""

from datetime import date

import numpy as np
import pytest
from PIL import Image

from app.domain.signals import SignalKind
from app.signals.face import FaceReading
from app.signals.pipeline import build_signals
from app.synthetic.cedula import CedulaData, render_back, render_front

TODAY = date(2026, 9, 14)


def persona() -> CedulaData:
    return CedulaData(
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


def cara(valor: float) -> FaceReading:
    """Una lectura con un embedding que apunta a donde digamos.

    Se construye a mano para poder fijar la similitud exacta: dos vectores
    unitarios cuyo producto escalar vale `valor`.
    """
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = valor
    vector[1] = float(np.sqrt(max(0.0, 1.0 - valor * valor)))
    return FaceReading(embedding=vector, side=200, confidence=0.9, faces_found=1)


class LectorSimulado:
    """Devuelve lo que se le diga, en el orden anverso, selfie."""

    def __init__(self, *respuestas: FaceReading) -> None:
        self._respuestas = list(respuestas)
        self.llamadas: list[bool] = []

    def read(self, source, *, allow_ghost: bool = False) -> FaceReading:
        self.llamadas.append(allow_ghost)
        return self._respuestas.pop(0)


@pytest.fixture
def documento():
    datos = persona()
    return render_front(datos), render_back(datos)


def facial(signals):
    return signals.get("facial.similarity")


def test_sin_selfie_la_senal_facial_no_existe_ni_como_no_disponible(documento):
    """No mandar selfie es una eleccion legitima, no un hueco.

    Listarla como no disponible le diria al agente que le falta algo que
    nadie penso darle, y le empujaria a pedir el reenvio de una foto que el
    solicitante no tenia que enviar.

    Hay ademas un motivo de medicion, y es el que lo destapo: los casos del
    catalogo no llevan selfie, asi que anadirles una senal ausente habria
    cambiado el prompt de todos a la vez que se estaba probando otro cambio
    en el prompt. Si el resultado se hubiera movido, no habria forma de
    saber cual de los dos cambios lo movio.
    """
    lector = LectorSimulado()

    senales = build_signals(*documento, today=TODAY, face_reader=lector)

    assert facial(senales) is None
    assert "facial.similarity" not in senales
    # Ni siquiera se carga el modelo: no hay nada que comparar.
    assert lector.llamadas == []


def test_el_juego_de_senales_sin_selfie_no_cambio_al_anadir_lo_facial(documento):
    """Fija el tamano del prompt que ven los casos de evaluacion.

    Si alguien vuelve a colar una senal en la rama sin selfie, este test lo
    para: cualquier senal nueva ahi invalida la comparacion con las
    mediciones ya publicadas, porque el agente estaria leyendo otro prompt.
    """
    senales = build_signals(*documento, today=TODAY)

    assert len(senales) == 28


def test_sin_cara_en_el_anverso_el_motivo_senala_al_documento(documento):
    lector = LectorSimulado(
        FaceReading(reason="no se detecto ninguna cara en la imagen"),
    )

    senales = build_signals(
        *documento,
        today=TODAY,
        selfie=Image.new("RGB", (400, 400), (200, 200, 200)),
        face_reader=lector,
    )

    senal = facial(senales)
    assert not senal.available
    assert "en el anverso del documento" in senal.unavailable_reason
    # El anverso se lee admitiendo el retrato fantasma; la selfie no llega
    # a leerse porque ya no hay nada que comparar.
    assert lector.llamadas == [True]


def test_sin_cara_en_la_selfie_el_motivo_senala_a_la_selfie(documento):
    lector = LectorSimulado(
        cara(0.8),
        FaceReading(reason="no se detecto ninguna cara en la imagen"),
    )

    senales = build_signals(
        *documento,
        today=TODAY,
        selfie=Image.new("RGB", (400, 400), (200, 200, 200)),
        face_reader=lector,
    )

    senal = facial(senales)
    assert not senal.available
    assert "en la selfie" in senal.unavailable_reason
    assert "en el anverso" not in senal.unavailable_reason
    # El anverso admite fantasma, la selfie no.
    assert lector.llamadas == [True, False]


def test_con_las_dos_caras_la_senal_lleva_la_similitud_y_ningun_umbral(documento):
    lector = LectorSimulado(cara(1.0), cara(0.8))

    senales = build_signals(
        *documento,
        today=TODAY,
        selfie=Image.new("RGB", (400, 400), (200, 200, 200)),
        face_reader=lector,
    )

    senal = facial(senales)
    assert senal.available
    assert senal.kind is SignalKind.SCORE
    assert senal.value == pytest.approx(0.8, abs=1e-3)
    # Un score, no un booleano: el corte lo pone quien decide, no quien mide.
    assert not isinstance(senal.value, bool)


def test_una_similitud_negativa_se_entrega_tal_cual(documento):
    """Dos caras pueden salir en negativo y eso es informacion, no un error.

    Redondear a cero, o a un minimo de cero, escondera que el modelo esta
    seguro de que NO son la misma persona, que es justo lo que interesa en
    un caso de suplantacion.
    """
    lector = LectorSimulado(cara(1.0), cara(-0.4))

    senales = build_signals(
        *documento,
        today=TODAY,
        selfie=Image.new("RGB", (400, 400), (200, 200, 200)),
        face_reader=lector,
    )

    assert facial(senales).value == pytest.approx(-0.4, abs=1e-3)
