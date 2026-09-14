"""Pruebas de la linea base de reglas fijas.

Las senales se construyen a mano en vez de sacarlas de imagenes: lo que se
prueba aqui es el arbol de decision, no el OCR, y mezclarlos haria que un
fallo del lector se leyera como un fallo de las reglas.

El orden de las reglas es lo que mas importa. Una calidad mala tiene que
ganar a una contradiccion, porque con la evidencia incompleta no se acusa
a nadie; invertir esos dos pasos convierte cada foto movida en una
acusacion de falsificacion.
"""

import pytest

from app.domain.citation_audit import audit_citations
from app.domain.decision import DecisionKind
from app.domain.signals import Signal, SignalKind, SignalSet
from app.evaluation.baseline import Thresholds, decide


def signals(**overrides) -> SignalSet:
    """Un caso limpio y aprobable, con los cambios que se pidan."""
    base = {
        "quality.front_sharpness": (SignalKind.COUNT, 2000),
        "quality.back_sharpness": (SignalKind.COUNT, 2900),
        "quality.front_glare": (SignalKind.SCORE, 0.05),
        "mrz.readable": (SignalKind.FLAG, True),
        "mrz.checks_ok": (SignalKind.FLAG, True),
        "ocr.fields_missing": (SignalKind.COUNT, 0),
        "document.expired": (SignalKind.FLAG, False),
        "cross.nuip": (SignalKind.TEXT, "match"),
        "cross.birth_date": (SignalKind.TEXT, "match"),
        "cross.expiry_date": (SignalKind.TEXT, "match"),
        "cross.surnames": (SignalKind.TEXT, "match"),
        "cross.given_names": (SignalKind.TEXT, "match"),
        "cross.sex": (SignalKind.TEXT, "match"),
    }
    base.update({k: (base[k][0], v) for k, v in overrides.items()})
    return SignalSet(
        Signal(sid, kind, f"Senal de prueba para {sid}, con texto suficiente.", value=value)
        for sid, (kind, value) in base.items()
    )


# --- Cada rama del arbol --------------------------------------------------


def test_todo_en_orden_se_aprueba():
    assert decide(signals()).decision is DecisionKind.APPROVE


def test_sin_mrz_legible_se_pide_otra_foto():
    decision = decide(signals(**{"mrz.readable": False}))

    assert decision.decision is DecisionKind.REQUEST_RESUBMISSION


def test_una_imagen_movida_se_pide_de_nuevo():
    decision = decide(signals(**{"quality.front_sharpness": 50}))

    assert decision.decision is DecisionKind.REQUEST_RESUBMISSION


def test_un_reflejo_fuerte_se_pide_de_nuevo():
    decision = decide(signals(**{"quality.front_glare": 0.95}))

    assert decision.decision is DecisionKind.REQUEST_RESUBMISSION


def test_los_digitos_descuadrados_con_buena_imagen_rechazan():
    decision = decide(signals(**{"mrz.checks_ok": False}))

    assert decision.decision is DecisionKind.REJECT


def test_una_contradiccion_con_buena_imagen_rechaza():
    decision = decide(signals(**{"cross.nuip": "mismatch"}))

    assert decision.decision is DecisionKind.REJECT


def test_un_documento_vencido_se_rechaza():
    decision = decide(signals(**{"document.expired": True}))

    assert decision.decision is DecisionKind.REJECT


# --- El orden de las reglas, que es lo que de verdad decide ---------------


def test_la_mala_calidad_gana_a_la_contradiccion():
    """Con la evidencia incompleta no se acusa a nadie.

    Si la contradiccion ganara, cada foto movida se convertiria en una
    acusacion de falsificacion, que es el error mas caro que puede cometer
    este sistema: el falso rechazo de una persona honesta.
    """
    decision = decide(
        signals(**{"quality.front_sharpness": 50, "cross.nuip": "mismatch"})
    )

    assert decision.decision is DecisionKind.REQUEST_RESUBMISSION


def test_un_campo_sin_localizar_gana_a_la_contradiccion():
    """El caso medido: con la fecha de nacimiento tapada, el OCR leyo en su
    sitio la de expedicion y produjo una contradiccion que no existia."""
    decision = decide(
        signals(**{"ocr.fields_missing": 1, "cross.birth_date": "mismatch"})
    )

    assert decision.decision is DecisionKind.REQUEST_RESUBMISSION


def test_la_contradiccion_gana_a_la_caducidad():
    """Un documento manipulado Y vencido se rechaza por lo primero, que es
    lo que hay que explicarle a quien lo presento."""
    decision = decide(
        signals(**{"cross.nuip": "mismatch", "document.expired": True})
    )

    assert decision.decision is DecisionKind.REJECT
    assert any(g.signal_id.startswith("cross.") for g in decision.groundings)


# --- La explicacion de la linea base --------------------------------------


@pytest.mark.parametrize(
    "cambios",
    [
        {},
        {"mrz.readable": False},
        {"quality.front_sharpness": 50},
        {"quality.front_glare": 0.95},
        {"mrz.checks_ok": False},
        {"cross.nuip": "mismatch"},
        {"document.expired": True},
        {"ocr.fields_missing": 2},
    ],
)
def test_la_linea_base_cita_igual_que_el_agente(cambios):
    """Pasa por el mismo verificador de citas que el agente.

    Si la linea base pudiera explicarse sin citar senales verificables, la
    comparacion entre las dos seria injusta a su favor: se le estaria
    exigiendo al agente un rigor que a ella no.
    """
    conjunto = signals(**cambios)
    decision = decide(conjunto)
    report = audit_citations(decision, conjunto)

    assert report.faithful, report.invalid


# --- Los umbrales son una eleccion, no una ley ---------------------------


def test_los_umbrales_se_pueden_cambiar_desde_fuera():
    """Estan como parametros a proposito: se eligieron sobre nueve casos y
    cada uno se apoya en dos o tres puntos."""
    caso = signals(**{"quality.front_glare": 0.7})

    assert decide(caso, Thresholds(max_glare=0.8)).decision is DecisionKind.APPROVE
    assert (
        decide(caso, Thresholds(max_glare=0.6)).decision
        is DecisionKind.REQUEST_RESUBMISSION
    )
