"""Pruebas de la lectura de la MRZ.

El riesgo que hay que vigilar aqui no es que el OCR se equivoque -- se
equivoca, y esta medido en scripts/measure_mrz_ocr.py -- sino que las
correcciones que arreglan sus fallos acaben **fabricando la validez que
deberian comprobar**.  Un corrector suficientemente entusiasta convierte
cualquier MRZ manipulada en una valida, y entonces la senal mas dura del
proyecto deja de existir sin que nadie lo note.
"""

from datetime import date

import pytest

from app.signals.mrz import parse_mrz
from app.signals.ocr_mrz import (
    _fix_alpha_fields,
    _repair,
    read_mrz,
)
from app.synthetic.cedula import CedulaData, render_back, render_front
from app.synthetic.degradation import blur, downscale
from app.synthetic.tampering import break_check_digit, retouch_mrz


def person(**changes) -> CedulaData:
    base = CedulaData(
        nuip="1020483236",
        document_number="087258257",
        surnames="OSSA DORIA",
        given_names="JUAN DIEGO",
        birth_date=date(1998, 1, 14),
        birth_place="MEDELLIN (ANTIOQUIA)",
        sex="M",
        height_m=1.63,
        blood_group="O+",
        issue_date=date(2016, 1, 21),
        issue_place="BELLO",
        expiry_date=date(2036, 1, 8),
    )
    return base.with_changes(**changes) if changes else base


# --- Lo que no debe pasar nunca ------------------------------------------


def test_una_mrz_manipulada_no_se_repara_hasta_parecer_valida():
    """El test que sostiene la ADR-0003.

    Si la correccion de errores de OCR pudiera arreglar una MRZ con un
    digito roto, el sistema declararia valido un documento manipulado y la
    unica senal con certeza del proyecto se volveria ruido.
    """
    data = person()
    rota = break_check_digit(data.mrz(), "compuesto")

    lectura = read_mrz(render_back(data, mrz_lines=rota))
    parsed = lectura.parsed()

    assert parsed is not None
    assert not parsed.checks_ok
    assert "compuesto" in parsed.failed_checks


def test_una_mrz_que_contradice_el_anverso_se_lee_tal_cual():
    """Aqui los digitos SI cuadran -- la MRZ es coherente consigo misma --
    y lo que falla es el cotejo con el anverso. La lectura no debe tocar
    nada: su trabajo es entregar lo que pone, no juzgarlo."""
    data, mrz_manipulada = retouch_mrz(person(), expiry_date=date(2040, 1, 8))

    lectura = read_mrz(render_back(data, mrz_lines=mrz_manipulada))
    parsed = lectura.parsed()

    assert parsed is not None
    assert parsed.checks_ok
    assert parsed.expiry_date == date(2040, 1, 8)
    assert parsed.expiry_date != data.expiry_date


def test_la_reparacion_timida_no_acepta_dos_cambios():
    """Solo se acepta una correccion si UN caracter cambiado hace cuadrar
    los cuatro digitos. Con dos errores simultaneos hay demasiadas
    combinaciones que cuadran por azar y la correccion dejaria de ser
    fiable."""
    lineas = person().mrz()
    rota = break_check_digit(break_check_digit(lineas, "compuesto"), "fecha_nacimiento")

    assert _repair(rota) is None


# --- La correccion estructural -------------------------------------------


def test_la_nacionalidad_leida_con_un_cero_se_corrige():
    """El fallo dominante medido: 'COL' leido 'C0L'. Ningun digito de
    control lo cubre, asi que sin esta correccion pasaba inadvertido en 52
    de 70 lecturas."""
    lineas = person().mrz()
    con_error = list(lineas)
    con_error[1] = con_error[1][:15] + "C0L" + con_error[1][18:]

    corregidas = _fix_alpha_fields(con_error)

    assert corregidas[1][15:18] == "COL"
    assert parse_mrz(corregidas).checks_ok


def test_la_nacionalidad_con_dos_errores_necesita_las_dos_reglas():
    """Las dos correcciones del codigo de pais no son redundantes.

    Con un solo error ('C0L') cualquiera de las dos lo arregla. Con dos
    ('C0I') hacen falta las dos: primero la regla de campos alfabeticos
    convierte el 0 en O, y solo entonces queda a un caracter de 'COL' para
    que actue la regla de distancia. Sin la primera, la segunda ve una
    distancia de dos y no toca nada.

    Corregir dos errores aqui sigue siendo seguro: la nacionalidad no entra
    en el payload de ningun digito de control, asi que ninguna correccion
    puede fabricar una validez aritmetica.
    """
    lineas = person().mrz()
    con_dos_errores = list(lineas)
    con_dos_errores[1] = con_dos_errores[1][:15] + "C0I" + con_dos_errores[1][18:]

    assert _fix_alpha_fields(con_dos_errores)[1][15:18] == "COL"


def test_una_letra_en_una_fecha_se_corrige_a_cifra():
    """Una letra en una fecha es siempre un fallo de lectura y nunca una
    manipulacion: quien edita una MRZ para falsificar escribe cifras."""
    lineas = person().mrz()
    con_error = list(lineas)
    con_error[1] = "98O1141" + con_error[1][7:]

    corregidas = _fix_alpha_fields(con_error)

    assert corregidas[1][:6] == "980114"


def test_un_pais_que_no_es_colombia_no_se_convierte_en_colombia():
    """La correccion del codigo de pais solo actua a un caracter de
    distancia. Si arreglara cualquier cosa, un documento extranjero saldria
    convertido en colombiano sin que nadie se enterara."""
    lineas = person().mrz()
    extranjero = list(lineas)
    extranjero[1] = extranjero[1][:15] + "USA" + extranjero[1][18:]

    corregidas = _fix_alpha_fields(extranjero)

    assert corregidas[1][15:18] == "USA"


def test_la_correccion_alfabetica_no_toca_el_numero_de_documento():
    """El numero vive en un campo numerico y lo cubre un digito de control;
    tocarlo con las reglas de los campos alfabeticos podria fabricar
    validez."""
    lineas = person().mrz()
    corregidas = _fix_alpha_fields(lineas)

    assert corregidas[0][5:15] == lineas[0][5:15]


# --- Lectura de imagenes -------------------------------------------------


def test_se_lee_una_cedula_limpia():
    lectura = read_mrz(render_back(person()))
    parsed = lectura.parsed()

    assert lectura.ok
    assert parsed is not None
    assert parsed.identity_number == "1020483236"
    assert parsed.birth_date == date(1998, 1, 14)
    assert parsed.expiry_date == date(2036, 1, 8)


@pytest.mark.parametrize(
    "degradacion", [lambda i: blur(i, 1.5), lambda i: downscale(i, 2.0)]
)
def test_se_lee_con_degradaciones_suaves(degradacion):
    lectura = read_mrz(degradacion(render_back(person())))
    parsed = lectura.parsed()

    assert parsed is not None
    assert parsed.identity_number == "1020483236"


def test_un_reverso_ilegible_da_un_error_explicado_y_no_una_excepcion():
    """Que no haya MRZ legible es informacion, no un fallo del programa: el
    agente tiene que poder decidir sabiendo que le falta esa senal."""
    lectura = read_mrz(blur(downscale(render_back(person()), 8.0), 4.0))

    assert not lectura.ok
    assert "lineas con forma de MRZ" in (lectura.error or "")
    assert lectura.parsed() is None


def test_el_anverso_no_tiene_mrz_y_se_dice_asi():
    lectura = read_mrz(render_front(person()))

    assert not lectura.ok or lectura.parsed() is None
