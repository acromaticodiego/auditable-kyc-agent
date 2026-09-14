"""Pruebas de las manipulaciones de documento.

Lo que hay que proteger es que cada manipulacion deje **exactamente el
rastro que dice dejar**.  Una que rompiera mas de la cuenta convertiria un
caso sutil en uno evidente, y una que no rompiera nada produciria un caso
etiquetado como fraude que en realidad esta bien.  En los dos sentidos el
conjunto mediria otra cosa distinta de la que dice.
"""

from datetime import date

import pytest

from app.signals.mrz import parse_mrz
from app.synthetic.cedula import CedulaData
from app.synthetic.tampering import (
    CHECK_POSITIONS,
    break_check_digit,
    expire,
    forge_consistently,
    retouch_front,
    retouch_mrz,
)


def person() -> CedulaData:
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


# --- Retoque del anverso, que es el fraude frecuente ----------------------


def test_retocar_el_anverso_deja_la_mrz_delatandolo():
    impreso, mrz_lines = retouch_front(person(), nuip="1098765432")
    mrz = parse_mrz(mrz_lines)

    # Lo impreso cambio.
    assert impreso.nuip == "1098765432"
    # La MRZ sigue siendo la de antes y cuadra consigo misma: el rastro no
    # esta en la aritmetica, esta en que las dos copias del dato difieren.
    assert mrz.checks_ok
    assert mrz.identity_number == "1234567890"
    assert mrz.identity_number != impreso.nuip


def test_retocar_la_fecha_impresa_la_descuadra_con_la_mrz():
    impreso, mrz_lines = retouch_front(person(), birth_date=date(1998, 4, 15))
    mrz = parse_mrz(mrz_lines)

    assert impreso.birth_date == date(1998, 4, 15)
    assert mrz.birth_date == date(2004, 4, 15)
    assert mrz.checks_ok


# --- Retoque de la MRZ, el caso contrario --------------------------------


def test_retocar_la_mrz_la_deja_coherente_pero_contradiciendo_el_anverso():
    """Los digitos de control no lo ven: solo el cotejo con el anverso."""
    impreso, mrz_lines = retouch_mrz(person(), expiry_date=date(2038, 4, 19))
    mrz = parse_mrz(mrz_lines)

    assert impreso.expiry_date == date(2032, 4, 19)
    assert mrz.expiry_date == date(2038, 4, 19)
    assert mrz.checks_ok


# --- La falsificacion que no deja rastro ---------------------------------


def test_una_falsificacion_coherente_no_deja_nada_que_detectar():
    """El techo del sistema, medido a proposito.

    Si este caso no estuviera en el conjunto, la tasa de deteccion de
    fraude mediria solo los fraudes torpes y se presentaria como si midiera
    todos.
    """
    impreso, mrz_lines = forge_consistently(person(), nuip="1098765432")
    mrz = parse_mrz(mrz_lines)

    assert mrz.checks_ok
    assert mrz.identity_number == impreso.nuip == "1098765432"
    assert mrz.failed_checks == ()


# --- Romper un digito de control -----------------------------------------


@pytest.mark.parametrize("cual", sorted(CHECK_POSITIONS))
def test_romper_un_digito_rompe_ese_digito_y_no_los_datos(cual):
    original = person().mrz()
    roto = break_check_digit(original, cual)

    assert cual in parse_mrz(roto).failed_checks
    # Los datos no se tocan: solo el digito impreso.
    assert parse_mrz(roto).identity_number == parse_mrz(original).identity_number
    assert parse_mrz(roto).birth_date == parse_mrz(original).birth_date


@pytest.mark.parametrize("cual", sorted(CHECK_POSITIONS))
def test_romper_un_digito_no_puede_acertar_por_casualidad(cual):
    """Se suma uno al digito en vez de poner una cifra al azar.

    Con un valor aleatorio, una de cada diez veces saldria el digito
    correcto y el caso quedaria etiquetado como fraude siendo valido.
    """
    roto = break_check_digit(person().mrz(), cual)
    check = next(c for c in parse_mrz(roto).checks if c.name == cual)

    assert not check.ok


@pytest.mark.parametrize(
    "cual", ["numero_documento", "fecha_nacimiento", "fecha_expiracion"]
)
def test_romper_el_digito_de_un_campo_arrastra_el_compuesto(cual):
    """Propiedad del formato TD1 que conviene tener anotada.

    El payload del digito compuesto incluye los digitos de control de cada
    campo, no solo los datos.  Asi que tocar uno de ellos rompe dos
    comprobaciones: la suya y la compuesta.  Falsificar sin dejar rastro
    obliga a recalcularlo todo, que es justo lo que encarece el fraude.
    """
    roto = break_check_digit(person().mrz(), cual)

    assert set(parse_mrz(roto).failed_checks) == {cual, "compuesto"}


def test_romper_el_compuesto_solo_rompe_el_compuesto():
    """El unico digito que no arrastra a nadie, porque ninguno lo incluye.

    Es la manipulacion mas sutil que se puede hacer sobre la MRZ y por eso
    es la que usa el caso del catalogo."""
    roto = break_check_digit(person().mrz(), "compuesto")

    assert parse_mrz(roto).failed_checks == ("compuesto",)


def test_un_digito_desconocido_no_se_traga_en_silencio():
    with pytest.raises(ValueError, match="digito desconocido"):
        break_check_digit(person().mrz(), "huella_dactilar")


# --- Guardas -------------------------------------------------------------


@pytest.mark.parametrize(
    "funcion", [retouch_front, retouch_mrz, forge_consistently]
)
def test_una_manipulacion_sin_cambios_no_tiene_sentido(funcion):
    """Devolver el documento intacto produciria un caso etiquetado como
    fraude que es identico a uno legitimo."""
    with pytest.raises(ValueError, match="necesita al menos un campo"):
        funcion(person())


# --- Caducar no es manipular ---------------------------------------------


def test_un_documento_caducado_sigue_siendo_coherente():
    """No es fraude: es autentico y ya no vale.  Su MRZ tiene que cuadrar,
    porque si no el sistema lo confundiria con un documento manipulado."""
    caducado = expire(person(), date(2024, 4, 19))
    mrz = parse_mrz(caducado.mrz())

    assert mrz.checks_ok
    assert mrz.expiry_date == date(2024, 4, 19)
    assert mrz.identity_number == caducado.nuip
