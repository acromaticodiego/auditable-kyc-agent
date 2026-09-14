"""Pruebas del generador de cedulas sinteticas.

Lo que de verdad hay que proteger aqui no es que las imagenes salgan
bonitas, sino que **el documento sea coherente consigo mismo**: la MRZ
tiene que cuadrar con lo impreso en el anverso.  Un caso etiquetado como
legitimo cuya MRZ no cuadre seria fraude sin querer, el sistema lo marcaria
con razon y la etiqueta diria lo contrario.  A partir de ahi el conjunto
de evaluacion mide ruido.
"""

from datetime import date

import pytest

from app.signals.mrz import parse_mrz
from app.synthetic.cedula import (
    HEIGHT,
    WIDTH,
    CedulaData,
    format_date_es,
    format_nuip,
    render_back,
    render_front,
)


def sample() -> CedulaData:
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


def region_bytes(image, box) -> bytes:
    return image.crop(box).tobytes()


# --- Coherencia entre el anverso y la MRZ ----------------------------------


def test_la_mrz_generada_cuadra_y_coincide_con_el_anverso():
    data = sample()
    mrz = parse_mrz(data.mrz())

    assert mrz.checks_ok
    assert mrz.identity_number == data.nuip
    assert mrz.birth_date == data.birth_date
    assert mrz.expiry_date == data.expiry_date
    assert mrz.sex == data.sex
    assert mrz.surnames == data.surnames
    assert mrz.given_names == data.given_names


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("nuip", "9876543210"),
        ("birth_date", date(1990, 2, 3)),
        ("expiry_date", date(2040, 7, 1)),
        ("document_number", "000999888"),
        ("surnames", "GOMEZ"),
    ],
)
def test_cambiar_un_dato_del_anverso_cambia_la_mrz(campo, valor):
    """Si la MRZ no siguiera al dato, el generador produciria documentos
    incoherentes sin avisar y el conjunto quedaria mal etiquetado."""
    original = sample()
    modificada = original.with_changes(**{campo: valor})

    assert modificada.mrz() != original.mrz()
    assert parse_mrz(modificada.mrz()).checks_ok


def test_el_nuip_se_imprime_con_puntos_y_viaja_sin_ellos():
    """Esa asimetria es del documento real, y el cotejo entre las dos copias
    tiene que normalizar.  Si el generador imprimiera el NUIP sin puntos, el
    cotejo pareceria funcionar en pruebas y fallaria con documentos de
    verdad."""
    data = sample()

    assert format_nuip(data.nuip) == "1.234.567.890"
    assert parse_mrz(data.mrz()).identity_number == "1234567890"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("1234567890", "1.234.567.890"),
        ("1020483236", "1.020.483.236"),
        ("12345", "12.345"),
        ("123", "123"),
        ("1.234.567.890", "1.234.567.890"),
    ],
)
def test_los_puntos_de_millar_se_agrupan_de_tres_en_tres_desde_la_derecha(
    valor, esperado
):
    assert format_nuip(valor) == esperado


def test_las_fechas_usan_los_meses_abreviados_en_espanol():
    assert format_date_es(date(1998, 1, 14)) == "14 ENE 1998"
    assert format_date_es(date(2032, 12, 5)) == "05 DIC 2032"


# --- Las imagenes ----------------------------------------------------------


def test_el_anverso_y_el_reverso_tienen_el_tamano_de_una_id_1():
    data = sample()

    assert render_front(data).size == (WIDTH, HEIGHT)
    assert render_back(data).size == (WIDTH, HEIGHT)


@pytest.mark.parametrize(
    ("campo", "valor", "caja"),
    [
        ("surnames", "GOMEZ", (330, 126, 700, 180)),
        ("given_names", "ANDRES", (330, 196, 700, 250)),
        ("nuip", "9876543210", (600, 80, 1000, 120)),
        ("birth_date", date(1990, 2, 3), (330, 336, 560, 390)),
    ],
)
def test_cada_campo_se_dibuja_en_su_sitio(campo, valor, caja):
    """Ancla la posicion de cada campo.

    Sin esto, un campo que dejara de dibujarse o que se moviera encima de
    otro pasaria inadvertido, y el OCR leeria mal por culpa del generador y
    no del ruido que se pretende simular.
    """
    original = render_front(sample())
    modificada = render_front(sample().with_changes(**{campo: valor}))

    assert region_bytes(original, caja) != region_bytes(modificada, caja)


def test_cambiar_un_campo_no_pisa_los_demas():
    """El anverso esta apretado y ya hubo dos solapamientos reales: la
    bandera sobre 'CIUDADANÍA' y luego sobre la 'R' de 'REPÚBLICA'."""
    original = render_front(sample())
    modificada = render_front(sample().with_changes(surnames="GOMEZ"))

    # La zona de 'Nombres', justo debajo, no debe cambiar.
    assert region_bytes(original, (330, 196, 700, 250)) == region_bytes(
        modificada, (330, 196, 700, 250)
    )


def test_el_reverso_acepta_una_mrz_impuesta():
    """Es lo que permite construir un caso de fraude: anverso intacto y MRZ
    que no le corresponde."""
    data = sample()
    otra = data.with_changes(nuip="9999999999").mrz()

    coherente = render_back(data)
    manipulado = render_back(data, mrz_lines=otra)

    assert region_bytes(coherente, (60, 400, 980, 580)) != region_bytes(
        manipulado, (60, 400, 980, 580)
    )
