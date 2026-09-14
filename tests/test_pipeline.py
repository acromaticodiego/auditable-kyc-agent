"""Pruebas del cotejo y del conjunto de senales.

El cotejo es donde el documento se comprueba contra si mismo. Lo que hay
que proteger es que una contradiccion se note, que una lectura que falta no
se confunda con una contradiccion, y que las senales lleguen al agente con
la informacion suficiente para distinguir las dos cosas.
"""

from datetime import date

import pytest

from app.evaluation.catalog import TODAY, build_catalog, person
from app.signals.cross_check import CrossStatus, cross_check, is_expired
from app.signals.mrz import parse_mrz
from app.signals.ocr_front import Field, FrontFields
from app.signals.pipeline import build_signals, contradictions


def fields(**valores) -> FrontFields:
    return FrontFields({k: Field(v, 0.95) for k, v in valores.items()})


def mrz_of(**changes):
    return parse_mrz(person(**changes).mrz() if changes else person().mrz())


def status_of(resultados, campo) -> CrossStatus:
    return next(r.status for r in resultados if r.field == campo)


# --- El cotejo ------------------------------------------------------------


def test_lo_que_coincide_se_marca_como_coincidente():
    front = fields(
        nuip="1.234.567.890",
        birth_date="15 ABR 2004",
        expiry_date="19 ABR 2032",
        surnames="WALTEROS",
        given_names="LAURA",
        sex="F",
    )
    resultados = cross_check(front, mrz_of())

    assert all(r.status is CrossStatus.MATCH for r in resultados)


def test_un_numero_retocado_en_el_anverso_se_contradice_con_la_mrz():
    """El fraude frecuente: se edita lo que se ve y se olvida el reverso."""
    resultados = cross_check(fields(nuip="1.098.765.432"), mrz_of())

    assert status_of(resultados, "nuip") is CrossStatus.MISMATCH


def test_un_campo_que_no_se_leyo_no_es_una_contradiccion():
    """Distinguirlos es el corazon del proyecto: una foto mala lleva a pedir
    otra, y una contradiccion lleva a rechazar. Confundirlas es acusar a
    alguien de falsificar por haber hecho una foto movida."""
    resultados = cross_check(fields(), mrz_of())

    assert status_of(resultados, "nuip") is CrossStatus.FRONT_MISSING
    assert status_of(resultados, "birth_date") is CrossStatus.FRONT_MISSING


def test_sin_mrz_legible_el_cotejo_lo_dice_en_vez_de_callar():
    resultados = cross_check(fields(nuip="1.234.567.890"), None)

    assert status_of(resultados, "nuip") is CrossStatus.MRZ_MISSING


def test_un_nombre_largo_truncado_por_la_mrz_no_es_una_contradiccion():
    """La tercera linea del TD1 tiene 30 caracteres para apellidos y nombres
    juntos, asi que un nombre largo llega cortado. Exigir igualdad exacta
    marcaria como sospechosa a toda persona con nombre largo, y eso es un
    limite del formato, no una contradiccion del documento."""
    largo = person(
        surnames="ARANGO LOPEZ DE VILLAMIZAR", given_names="MARIA FERNANDA"
    )
    front = fields(surnames="ARANGO LOPEZ DE VILLAMIZAR")
    resultados = cross_check(front, parse_mrz(largo.mrz()))

    assert status_of(resultados, "surnames") is CrossStatus.MATCH


def test_la_vigencia_se_decide_con_la_fecha_de_la_mrz():
    """La de la MRZ lleva un digito de control detras; la del anverso no."""
    caducada = mrz_of(expiry_date=date(2024, 4, 19))

    assert is_expired(fields(expiry_date="19 ABR 2032"), caducada, TODAY) is True


def test_sin_mrz_la_vigencia_se_decide_con_el_anverso():
    assert is_expired(fields(expiry_date="19 ABR 2024"), None, TODAY) is True
    assert is_expired(fields(expiry_date="19 ABR 2032"), None, TODAY) is False


def test_sin_ninguna_fecha_la_vigencia_queda_sin_saber():
    """Devolver False seria afirmar que esta vigente sin haberlo mirado."""
    assert is_expired(fields(), None, TODAY) is None


# --- El conjunto de senales ----------------------------------------------


def test_un_caso_legitimo_no_produce_contradicciones():
    caso = next(c for c in build_catalog() if c.id == "legitimo-limpio")
    senales = build_signals(*caso.build(), today=TODAY)

    assert contradictions(senales) == []
    assert senales.get("mrz.checks_ok").value is True
    assert senales.get("document.expired").value is False


def test_el_nuip_retocado_aparece_como_contradiccion_en_las_senales():
    caso = next(c for c in build_catalog() if c.id == "fraude-nuip-retocado-anverso")
    senales = build_signals(*caso.build(), today=TODAY)

    assert contradictions(senales) == ["nuip"]
    # Y los digitos de la MRZ siguen cuadrando: la manipulacion no esta en
    # la MRZ, esta en el desacuerdo entre las dos copias.
    assert senales.get("mrz.checks_ok").value is True


def test_un_reverso_ilegible_deja_las_senales_de_mrz_no_disponibles():
    """No disponible no es lo mismo que False. Que la MRZ no se pueda leer
    tiene que llegar al agente como ausencia, para que pueda pedir otra foto
    en vez de decidir sin ella."""
    caso = next(c for c in build_catalog() if c.id == "captura-reverso-ilegible")
    senales = build_signals(*caso.build(), today=TODAY)

    assert senales.get("mrz.readable").value is False
    assert not senales.get("mrz.checks_ok").available
    assert senales.get("mrz.checks_ok").unavailable_reason


def test_toda_senal_publicada_lleva_descripcion():
    """El identificador solo no dice si 2033 es mucho o poco."""
    caso = next(c for c in build_catalog() if c.id == "legitimo-limpio")

    for senal in build_signals(*caso.build(), today=TODAY):
        assert len(senal.description) > 30, senal.id


def test_los_identificadores_de_senal_no_se_repiten():
    caso = next(c for c in build_catalog() if c.id == "legitimo-limpio")
    senales = build_signals(*caso.build(), today=TODAY)

    identificadores = [s.id for s in senales]
    assert len(set(identificadores)) == len(identificadores)


@pytest.mark.parametrize(
    "caso_id", ["legitimo-limpio", "captura-borrosa-fuerte", "captura-reverso-ilegible"]
)
def test_ningun_caso_revienta_el_pipeline(caso_id):
    caso = next(c for c in build_catalog() if c.id == caso_id)
    senales = build_signals(*caso.build(), today=TODAY)

    assert len(senales) > 10
