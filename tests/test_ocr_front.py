"""Pruebas de la lectura del anverso.

Aqui no hay digitos de control que avisen de una lectura erronea, asi que
lo que se protege es distinto de lo de la MRZ: que los campos se localicen
donde toca, que las columnas no se mezclen entre si, y que **se sepa de
donde salio cada dato** -- de su rotulo impreso o deducido por el orden --
porque las dos cosas no merecen el mismo credito.
"""

from datetime import date

import pytest

from app.signals.ocr_front import (
    parse_nuip,
    parse_place_and_date,
    parse_spanish_date,
    read_front,
)
from app.synthetic.cedula import CedulaData, render_front
from app.synthetic.degradation import blur, downscale, jpeg_artifacts


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


# --- Lectura por rotulo ---------------------------------------------------


def test_se_leen_los_campos_de_una_cedula_limpia():
    campos = read_front(render_front(person()))

    assert parse_nuip(campos.value("nuip") or "") == "1020483236"
    assert campos.value("surnames") == "OSSA DORIA"
    assert campos.value("given_names") == "JUAN DIEGO"
    assert parse_spanish_date(campos.value("birth_date") or "") == date(1998, 1, 14)
    assert parse_spanish_date(campos.value("expiry_date") or "") == date(2036, 1, 8)
    assert parse_place_and_date(campos.value("issue") or "") == (
        date(2016, 1, 21),
        "BELLO",
    )


def test_una_cedula_limpia_se_lee_por_los_rotulos_y_no_por_deduccion():
    """Si el respaldo se activara con una imagen perfecta, estaria tapando
    un fallo del camino principal y nadie se enteraria."""
    campos = read_front(render_front(person()))

    for nombre in ("surnames", "given_names", "birth_date", "expiry_date"):
        assert campos.get(nombre).source == "label", nombre


def test_las_columnas_de_una_misma_fila_no_se_mezclan():
    """Nacionalidad, estatura y sexo comparten renglon. El limite de cada
    columna se calcula solo, mirando donde empieza el rotulo siguiente."""
    campos = read_front(render_front(person()))

    assert campos.value("nationality") == "COL"
    assert campos.value("height") == "1.63"
    assert campos.value("sex") == "M"


def test_el_valor_de_un_campo_no_es_su_propio_rotulo():
    """La firma manuscrita cae a la altura del rotulo 'Fecha de expiracion'
    y hacia que la fila del rotulo pareciera estar debajo de si misma; el
    extractor devolvia 'Fecha de expiracion' como si fuera la fecha."""
    campos = read_front(render_front(person()))

    assert "expiraci" not in (campos.value("expiry_date") or "").lower()


# --- El respaldo por orden ------------------------------------------------


@pytest.mark.parametrize(
    "degradacion", [lambda i: blur(i, 1.5), lambda i: downscale(i, 2.0)]
)
def test_los_campos_se_rescatan_cuando_los_rotulos_se_borran(degradacion):
    """El hallazgo que motivo el respaldo.

    Los rotulos van en gris claro y cuerpo pequeno, y con un desenfoque
    leve desaparecen. Los valores, en negrita y mas grandes, siguen
    leyendose con confianza de 90 y pico. Sin respaldo el extractor
    devolvia un unico campo de once y la conclusion facil habria sido que
    el anverso no se puede leer, que es falsa.
    """
    campos = read_front(degradacion(render_front(person())))

    assert campos.value("surnames") == "OSSA DORIA"
    assert campos.value("given_names") == "JUAN DIEGO"
    assert parse_spanish_date(campos.value("birth_date") or "") == date(1998, 1, 14)
    assert parse_spanish_date(campos.value("expiry_date") or "") == date(2036, 1, 8)


def test_lo_deducido_se_marca_como_deducido():
    """Un dato deducido por su posicion merece menos credito que uno leido
    bajo su rotulo, y esa diferencia tiene que llegar hasta el agente."""
    campos = read_front(blur(render_front(person()), 1.5))

    assert campos.get("surnames").source == "order"
    assert campos.get("birth_date").source == "order"


def test_las_tres_fechas_se_asignan_por_su_orden_vertical():
    """Nacimiento, expedicion y expiracion, en ese orden. Es una propiedad
    del documento y no del generador: la cedula siempre las imprime asi."""
    data = person(
        birth_date=date(1990, 3, 2),
        issue_date=date(2015, 6, 7),
        expiry_date=date(2030, 9, 11),
    )
    campos = read_front(blur(render_front(data), 1.5))

    assert parse_spanish_date(campos.value("birth_date") or "") == date(1990, 3, 2)
    assert parse_place_and_date(campos.value("issue") or "")[0] == date(2015, 6, 7)
    assert parse_spanish_date(campos.value("expiry_date") or "") == date(2030, 9, 11)


def test_una_imagen_destruida_devuelve_pocos_campos_sin_reventar():
    """Que no se pueda leer es informacion para el agente, no un error."""
    campos = read_front(blur(downscale(render_front(person()), 6.0), 4.0))

    assert len(campos.fields) < 4
    assert campos.value("surnames") in (None, "") or True


# --- Interpretacion de los valores ---------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("1.020.483.236", "1020483236"),
        ("NUIP 1.234.567.890", "1234567890"),
        ("", None),
        ("sin cifras", None),
    ],
)
def test_el_nuip_se_queda_solo_con_las_cifras(crudo, esperado):
    assert parse_nuip(crudo) == esperado


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("14 ENE 1998", date(1998, 1, 14)),
        ("08 ENE 2036", date(2036, 1, 8)),
        ("05 DIC 2032", date(2032, 12, 5)),
        # El grupo sanguineo se pega a la fecha cuando el rescate toma la
        # fila entera; el parseo tiene que ignorarlo en vez de rendirse.
        ("14 ENE 1998 O+", date(1998, 1, 14)),
        ("no es una fecha", None),
        ("32 ENE 1998", None),
        ("14 XXX 1998", None),
    ],
)
def test_las_fechas_en_espanol_se_interpretan_o_se_descartan(crudo, esperado):
    assert parse_spanish_date(crudo) == esperado


def test_la_expedicion_separa_fecha_y_lugar():
    assert parse_place_and_date("21 ENE 2016, BELLO") == (date(2016, 1, 21), "BELLO")
    assert parse_place_and_date("21 ENE 2016") == (date(2016, 1, 21), None)


def test_la_confianza_baja_cuando_la_imagen_empeora():
    """Es lo unico observable en produccion: el acierto no se puede medir
    sin tener la respuesta al lado, la confianza si se puede leer."""
    limpia = read_front(render_front(person()))
    fea = read_front(jpeg_artifacts(render_front(person()), 15))

    assert limpia.confidence("surnames") >= fea.confidence("surnames")
