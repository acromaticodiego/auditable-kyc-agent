"""Comprueba que la lista de campos amparados diga la verdad.

`CROSS_COVERED_BY_CHECK_DIGIT` le dice al agente, en la descripcion de cada
cotejo, si la aritmetica de la MRZ respalda ese campo. Es un hecho del
formato, no una opinion, y de el depende que un 'mismatch' sea evidencia
dura o una duda entre dos lecturas.

Si la lista se desincronizara del calculo real, el agente recibiria una
afirmacion falsa sobre la evidencia que tiene delante y **nada fallaria**:
seguiria decidiendo, solo que sobre una premisa equivocada. Por eso no se
comprueba contra una lista escrita a mano sino contra los digitos de
control de verdad.
"""

from datetime import date

import pytest

from app.signals.pipeline import CROSS_COVERED_BY_CHECK_DIGIT
from app.synthetic.cedula import CedulaData

# Donde viven los cuatro digitos de control en un TD1.
POSICIONES = ((0, 14), (1, 6), (1, 14), (1, 29))


def base() -> CedulaData:
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


def digitos(datos: CedulaData) -> tuple[str, ...]:
    lineas = datos.mrz()
    return tuple(lineas[fila][columna] for fila, columna in POSICIONES)


@pytest.mark.parametrize(
    "campo, cambio",
    [
        ("nuip", {"nuip": "9876543210"}),
        ("birth_date", {"birth_date": date(2001, 1, 2)}),
        ("expiry_date", {"expiry_date": date(2031, 3, 4)}),
        ("surnames", {"surnames": "WALTEROZ"}),
        ("given_names", {"given_names": "LAURO"}),
        ("sex", {"sex": "M"}),
    ],
)
def test_la_lista_coincide_con_lo_que_de_verdad_protege_la_aritmetica(campo, cambio):
    """Cambiar un campo amparado mueve algun digito; uno sin amparo, ninguno.

    Es la definicion operativa de 'amparado' y se comprueba contra el
    calculo real de la MRZ, no contra otra lista.
    """
    antes = digitos(base())
    despues = digitos(base().with_changes(**cambio))

    algun_digito_cambio = antes != despues
    esta_en_la_lista = campo in CROSS_COVERED_BY_CHECK_DIGIT

    assert algun_digito_cambio == esta_en_la_lista, (
        f"{campo}: la lista dice amparado={esta_en_la_lista} pero cambiarlo "
        f"{'si' if algun_digito_cambio else 'no'} movio ningun digito"
    )


def test_el_sexo_y_los_nombres_son_el_punto_ciego_del_formato():
    """Lo afirma la ADR-0003 y aqui queda comprobado en vez de creido."""
    assert "sex" not in CROSS_COVERED_BY_CHECK_DIGIT
    assert "surnames" not in CROSS_COVERED_BY_CHECK_DIGIT
    assert "given_names" not in CROSS_COVERED_BY_CHECK_DIGIT
