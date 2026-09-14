"""Pruebas del catalogo y de la particion.

La particion es la pieza que impide repetir un error concreto: elegir un
umbral sobre unos datos y presentar como medida el resultado sobre esos
mismos datos.  Aqui se comprueba que la herramienta lo impide sola, no que
alguien se acuerde de respetarlo.
"""

from collections import Counter

import pytest

from app.domain.decision import DecisionKind
from app.evaluation.catalog import build_catalog, load_cases
from app.evaluation.split import (
    CALIBRATION,
    HOLDOUT,
    HoldoutLocked,
    check_access,
    split_of,
)


# --- El reservado se defiende solo ---------------------------------------


def test_pedir_el_reservado_sin_declararlo_falla():
    """Mirar el reservado "solo para ver como va" no deja rastro, asi que no
    puede depender de la disciplina de quien ejecuta la herramienta."""
    with pytest.raises(HoldoutLocked, match="no se mira para ajustar"):
        load_cases(HOLDOUT)


def test_el_reservado_se_abre_declarando_que_es_la_medicion_final():
    casos = load_cases(HOLDOUT, final_measurement=True)

    assert casos
    assert all(caso.split == HOLDOUT for caso in casos)


def test_la_calibracion_no_necesita_declarar_nada():
    casos = load_cases(CALIBRATION)

    assert casos
    assert all(caso.split == CALIBRATION for caso in casos)


def test_una_particion_inventada_no_pasa_por_reservada():
    """Sin esta comprobacion, un error tipografico como 'holdut' se colaria
    por la rama permisiva y devolveria la calibracion en silencio."""
    with pytest.raises(ValueError, match="particion desconocida"):
        check_access("holdut")


# --- La particion es estable ---------------------------------------------


def test_la_mitad_de_un_caso_depende_solo_de_su_identificador():
    """Si dependiera de la posicion en la lista, anadir un caso movería a
    otros de lado y las medidas anteriores dejarian de ser comparables."""
    assert split_of("legitimo-limpio") == split_of("legitimo-limpio")
    assert {split_of(f"caso-{n}") for n in range(50)} == {CALIBRATION, HOLDOUT}


def test_cada_caso_cae_en_una_mitad_y_solo_una():
    casos = build_catalog()
    calibracion = load_cases(CALIBRATION)
    reservado = load_cases(HOLDOUT, final_measurement=True)

    assert len(calibracion) + len(reservado) == len(casos)
    assert not {c.id for c in calibracion} & {c.id for c in reservado}


# --- Las dos mitades sirven para algo ------------------------------------


@pytest.mark.parametrize(
    ("split", "final"), [(CALIBRATION, False), (HOLDOUT, True)]
)
def test_cada_mitad_cubre_las_cuatro_decisiones(split, final):
    """Una mitad sin casos de rechazo, por ejemplo, daria una tasa de
    acierto que no dice nada sobre la capacidad de rechazar."""
    casos = load_cases(split, final_measurement=final)
    decisiones = {caso.expected_decision for caso in casos}

    assert DecisionKind.APPROVE in decisiones
    assert DecisionKind.REJECT in decisiones
    assert DecisionKind.REQUEST_RESUBMISSION in decisiones
    # El escalado se anadio tarde: el catalogo vivio dieciocho casos sin un
    # solo caso de revision humana, midiendo un sistema de tres salidas y
    # llamandolo de cuatro.
    assert DecisionKind.ESCALATE_TO_HUMAN in decisiones


@pytest.mark.parametrize(
    ("split", "final"), [(CALIBRATION, False), (HOLDOUT, True)]
)
def test_cada_mitad_tiene_algun_fraude(split, final):
    casos = load_cases(split, final_measurement=final)

    assert any(caso.is_fraud for caso in casos)


# --- El catalogo en si ---------------------------------------------------


def test_los_identificadores_no_se_repiten():
    identificadores = [caso.id for caso in build_catalog()]

    assert len(set(identificadores)) == len(identificadores)


def test_cada_caso_produce_un_anverso_y_un_reverso():
    for caso in build_catalog():
        front, back = caso.build()

        assert front.size[0] > 0 and front.size[1] > 0, caso.id
        assert back.size[0] > 0 and back.size[1] > 0, caso.id


def test_cada_caso_explica_su_etiqueta():
    """El campo `reason` existe para que alguien pueda leer una etiqueta y
    decir que esta mal.  Una razon de dos palabras no permite discutirla.

    A los casos discutibles -- los marcados como ambiguos y los fraudes que
    el sistema no puede ver -- se les exige bastante mas, porque son
    precisamente los que alguien va a querer rebatir.
    """
    for caso in build_catalog():
        assert len(caso.reason) >= 55, caso.id
        if caso.ambiguous or not caso.detectable:
            assert len(caso.reason) > 150, f"{caso.id}: razon demasiado corta"


def test_el_conjunto_incluye_un_fraude_que_el_sistema_no_puede_ver():
    """Un conjunto donde todos los fraudes son detectables mide el techo del
    sistema y lo llama acierto."""
    indetectables = [
        caso for caso in build_catalog() if caso.is_fraud and not caso.detectable
    ]

    assert indetectables
    # Y su decision esperada es aprobar, porque nada permite saberlo.
    assert all(
        caso.expected_decision is DecisionKind.APPROVE for caso in indetectables
    )


def test_un_fraude_indetectable_no_puede_esperarse_que_se_rechace():
    """Guarda contra una etiqueta incoherente: exigir que se rechace algo
    que por construccion no deja rastro seria pedirle al sistema que
    adivine, y penalizarlo por no hacerlo."""
    for caso in build_catalog():
        if not caso.detectable:
            assert caso.expected_decision is DecisionKind.APPROVE, caso.id


def test_hay_casos_de_las_tres_familias():
    etiquetas = Counter(tag for caso in build_catalog() for tag in caso.tags)

    assert etiquetas["legitimo"] >= 3
    assert etiquetas["fraude"] >= 3
    assert etiquetas["captura"] >= 3
