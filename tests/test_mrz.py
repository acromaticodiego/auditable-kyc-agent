"""Pruebas de la MRZ.

El ancla es el ejemplo TD1 de la especificacion ICAO 9303 parte 5, que es
una fuente externa: comprobar la implementacion con una MRZ generada por
la propia implementacion seria circular y pasaria en verde con el
algoritmo mal.

Ninguna MRZ real aparece aqui.  La implementacion se verifico una vez
contra un documento real fuera del repositorio (los cuatro digitos
cuadraban), pero ese documento no se versiona.
"""

from datetime import date

import pytest

from app.signals.mrz import (
    MrzError,
    character_value,
    build_td1,
    check_digit,
    parse_mrz,
    parse_mrz_date,
)

# Ejemplo canonico de ICAO 9303 parte 5.
ICAO_TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


def specimen() -> list[str]:
    """Una MRZ coherente con datos inventados."""
    return build_td1(
        document_number="000000012",
        birth_date=date(1988, 8, 21),
        sex="F",
        expiry_date=date(2031, 1, 30),
        identity_number="1234567890",
        surnames="WALTEROS",
        given_names="LAURA",
    )


def altered(lines: list[str], line: int, position: int, character: str) -> list[str]:
    copy = list(lines)
    row = copy[line]
    copy[line] = row[:position] + character + row[position + 1 :]
    return copy


# --- Contra la fuente externa ----------------------------------------------


def test_el_ejemplo_de_icao_valida_los_cuatro_digitos():
    mrz = parse_mrz(ICAO_TD1)

    assert mrz.checks_ok
    assert mrz.failed_checks == ()
    assert (mrz.document_number, mrz.issuing_country) == ("D23145890", "UTO")
    assert (mrz.surnames, mrz.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert mrz.birth_date == date(1974, 8, 12)
    assert mrz.expiry_date == date(2012, 4, 15)


def test_el_digito_de_control_usa_los_pesos_7_3_1():
    # Tomados del mismo ejemplo de ICAO, campo a campo.
    assert check_digit("D23145890") == 7
    assert check_digit("740812") == 2
    assert check_digit("120415") == 9


# --- Deteccion de manipulacion ---------------------------------------------


def test_una_mrz_construida_es_coherente_consigo_misma():
    mrz = parse_mrz(specimen())

    assert mrz.checks_ok
    assert mrz.identity_number == "1234567890"
    assert mrz.full_name == "LAURA WALTEROS"


def test_tocar_un_digito_del_numero_rompe_dos_comprobaciones():
    """El compuesto cubre los mismos campos que ya tienen digito propio, de
    modo que una manipulacion nunca rompe una sola cosa.  Eso es lo que
    hace cara la falsificacion: hay que recalcular todo."""
    mrz = parse_mrz(altered(specimen(), 0, 13, "9"))

    assert not mrz.checks_ok
    assert set(mrz.failed_checks) == {"numero_documento", "compuesto"}


def test_tocar_la_fecha_de_nacimiento_rompe_dos_comprobaciones():
    mrz = parse_mrz(altered(specimen(), 1, 3, "9"))

    assert set(mrz.failed_checks) == {"fecha_nacimiento", "compuesto"}


def test_tocar_solo_el_numero_de_identidad_rompe_el_compuesto():
    """El NUIP no tiene digito propio: si el compuesto no lo cubriera, se
    podria cambiar el numero de identidad de una cedula sin dejar rastro
    aritmetico, que es justo el fraude que esto pretende detectar."""
    mrz = parse_mrz(altered(specimen(), 1, 20, "9"))

    assert mrz.failed_checks == ("compuesto",)


# --- Lectura fallida frente a documento sospechoso -------------------------


@pytest.mark.parametrize(
    ("lines", "motivo"),
    [
        (["corta", "corta", "corta"], "caracteres"),
        (ICAO_TD1[:2], "lineas"),
        ([ICAO_TD1[0], ICAO_TD1[1], "ERIKSSON<<anna<maria<<<<<<<<<<"], "no son de MRZ"),
    ],
)
def test_una_mrz_mal_formada_es_un_error_de_lectura_no_una_senal(lines, motivo):
    """Distinguirlas importa: un OCR que lee mal produce basura, y contar
    eso como fraude acusaria de falsificar a quien solo hizo una foto
    mala."""
    with pytest.raises(MrzError, match=motivo):
        parse_mrz(lines)


# --- El siglo de una fecha de dos digitos ----------------------------------


def test_el_mismo_ano_es_pasado_como_nacimiento_y_futuro_como_expiracion():
    """'36' es 1936 si es una fecha de nacimiento y 2036 si es de
    expiracion.  La MRZ no dice el siglo, asi que lo resuelve el campo."""
    hoy = date(2026, 9, 14)

    assert parse_mrz_date("360108", prefer_past=True, today=hoy) == date(1936, 1, 8)
    assert parse_mrz_date("360108", prefer_past=False, today=hoy) == date(2036, 1, 8)


def test_un_documento_ya_caducado_sigue_siendo_legible():
    """Devolver None dejaria sin fecha justo al caso que hay que rechazar."""
    assert parse_mrz_date("200415", prefer_past=False, today=date(2026, 9, 14)) == date(
        2020, 4, 15
    )


def test_una_fecha_imposible_no_revienta():
    assert parse_mrz_date("999999", prefer_past=True) is None


def test_las_letras_valen_de_10_a_35_aunque_el_digito_no_lo_note():
    """Anclado aparte a proposito.

    El +10 de las letras no afecta a `check_digit`: desplaza la suma en 10
    por el peso, y 70, 30 y 10 son cero modulo 10.  Una implementacion con
    las letras de 0 a 25 pasaria todos los demas tests de este fichero.  Se
    fija aqui para que deje de ser invisible en cuanto alguien use
    `character_value` para otra cosa.
    """
    assert character_value("0") == 0
    assert character_value("9") == 9
    assert character_value("A") == 10
    assert character_value("D") == 13
    assert character_value("Z") == 35
    assert character_value("<") == 0


def test_un_caracter_imposible_en_una_mrz_se_rechaza():
    with pytest.raises(MrzError, match="caracter no valido"):
        character_value("ñ")


@pytest.mark.parametrize(
    ("raw", "prefer_past", "esperado"),
    [
        # Las dos opciones son pasado y la buena es la reciente.  Con la
        # primera version esto daba 1904: el generador de cedulas lo
        # destapo al construir una persona nacida en 2004.
        ("040415", True, date(2004, 4, 15)),
        ("980114", True, date(1998, 1, 14)),
        # Las dos son futuro imposible como nacimiento; gana la unica pasada.
        ("360108", True, date(1936, 1, 8)),
        # Como expiracion, la mas proxima de las futuras.
        ("320419", False, date(2032, 4, 19)),
        ("360108", False, date(2036, 1, 8)),
    ],
)
def test_el_siglo_se_elige_por_cercania_no_por_orden(raw, prefer_past, esperado):
    assert parse_mrz_date(raw, prefer_past=prefer_past, today=date(2026, 9, 14)) == esperado
