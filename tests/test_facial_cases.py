"""Pruebas del conjunto facial, que vive aparte del catalogo de 27 casos.

Estas pruebas NO dependen de que haya fotos reales, y esa es justo la
propiedad que fijan: el modulo tiene que poder importarse y ejecutarse en
una maquina recien clonada, donde `data/real/caras/` no existe, sin romper
la suite. Si lo hicieran, el catalogo principal dejaria de ser reproducible,
que es lo que estos casos viven fuera para evitar.
"""

import pytest

from app.domain.decision import DecisionKind
from app.evaluation import facial_cases


def test_sin_fotos_no_hay_casos_y_no_se_queja(tmp_path, monkeypatch):
    """Es el estado normal de una maquina recien clonada.

    Quien ejecute la medicion vera el aviso del script, no una excepcion a
    mitad de la suite.
    """
    monkeypatch.setattr(facial_cases, "CARAS", tmp_path / "no-existe")

    assert facial_cases.build_facial_cases() == []


def test_con_una_sola_identidad_tampoco_hay_casos(tmp_path, monkeypatch):
    """Un par necesita dos personas; con una no hay ni impostor ni legitimo."""
    sola = tmp_path / "persona-01"
    sola.mkdir(parents=True)
    (sola / "foto.jpg").write_bytes(b"da igual: no se llega a abrirla")
    monkeypatch.setattr(facial_cases, "CARAS", tmp_path)

    assert facial_cases.build_facial_cases() == []


@pytest.mark.skipif(
    not facial_cases._identidades(),
    reason="no hay fotos reales en data/real/caras/ en esta maquina",
)
def test_los_casos_construidos_declaran_lo_que_deberia_decidirse():
    """Solo corre donde estan las fotos; en el resto se salta y se dice.

    Un test que se salta en silencio seria peor que no tenerlo: pareceria
    cobertura que no existe.
    """
    casos = facial_cases.build_facial_cases()

    legitimos = [c for c in casos if not c.is_fraud]
    fraudes = [c for c in casos if c.is_fraud]

    for caso in legitimos:
        assert caso.expected_decision is DecisionKind.APPROVE
    for caso in fraudes:
        assert caso.expected_decision is DecisionKind.REJECT
        assert "ninguna otra senal" in caso.reason

    # El titular del documento es el mismo en todos los casos: asi lo unico
    # que cambia entre el legitimo y los impostores es la cara de la selfie.
    assert len({c.front.tobytes() for c in casos}) == 1
    assert len({c.id for c in casos}) == len(casos)
