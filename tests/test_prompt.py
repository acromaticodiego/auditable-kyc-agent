"""Pruebas del prompt que ve el agente.

Un prompt no se puede probar de verdad sin llamar al modelo, y llamarlo
cuesta cupo.  Lo que si se puede fijar aqui es lo que tiene que ser cierto
del texto pase lo que pase: que todas las senales lleguen, que lleguen con
el identificador exacto que luego se auditara, y que el menu de decisiones
no se desincronice del enumerado del dominio.

Ese ultimo es el que mas vale. El menu esta escrito a mano en castellano y
los valores viven en `DecisionKind`: anadir una decision al enumerado sin
contarsela al agente produce un sistema que promete cuatro salidas y solo
sabe usar tres, y no falla por ningun lado.
"""

import pytest

from app.agent.prompt import INSTRUCTIONS, build_prompt, render_signals
from app.domain.decision import DecisionKind, Weight
from app.domain.signals import UNAVAILABLE_MARKER, Signal, SignalKind, SignalSet


def sin_saltos(texto: str) -> str:
    """Colapsa los espacios para poder buscar frases que cruzan una linea.

    El prompt esta ajustado a 72 columnas, asi que una frase cualquiera
    puede partirse por la mitad. Buscarla tal cual convierte el test en una
    prueba de donde cae el salto de linea, que no es lo que se quiere fijar.
    """
    return " ".join(texto.split())


def conjunto() -> SignalSet:
    return SignalSet(
        [
            Signal("quality.front_sharpness", SignalKind.COUNT,
                   "Nitidez del anverso.", value=1505),
            Signal("mrz.checks_ok", SignalKind.FLAG,
                   "Los digitos de control de la MRZ cuadran.", value=True),
            Signal("ocr.full_name", SignalKind.TEXT,
                   "Nombre completo leido del anverso.", value="LAURA WALTEROS"),
            Signal("document.expiry_date", SignalKind.TEXT,
                   "Fecha de expiracion del documento.",
                   unavailable_reason="el campo quedo fuera del recorte"),
        ]
    )


@pytest.mark.parametrize("decision", list(DecisionKind))
def test_el_menu_nombra_todas_las_decisiones_posibles(decision):
    """Una decision en el enumerado que el prompt no menciona no existe.

    El agente solo puede devolver lo que se le ha explicado. Si se anade una
    salida al dominio y nadie toca este texto, el sistema promete cuatro
    caminos y sabe usar tres, sin que falle nada.
    """
    assert decision.value in INSTRUCTIONS


def test_todas_las_senales_llegan_con_su_identificador_y_su_valor():
    texto = render_signals(conjunto())

    assert "quality.front_sharpness" in texto
    assert "1505" in texto
    assert "mrz.checks_ok" in texto
    assert "LAURA WALTEROS" in texto


def test_una_senal_sin_medir_llega_con_el_marcador_y_el_motivo():
    """Ocultarla llevaria al agente a decidir sin saber que le falta algo.

    Y el marcador tiene que ser el mismo que acepta el auditor: si aqui se
    escribiera de otra forma, la cita mas honesta que puede hacer el modelo
    -- reconocer la ausencia -- se contaria como cita falsa.
    """
    texto = render_signals(conjunto())

    assert UNAVAILABLE_MARKER in texto
    assert "el campo quedo fuera del recorte" in texto
    # Y no se le atribuye ningun valor inventado.
    assert "document.expiry_date (text) = NO DISPONIBLE" in texto


def test_el_prompt_lleva_las_instrucciones_y_las_senales():
    prompt = build_prompt(conjunto())

    assert prompt.startswith(INSTRUCTIONS)
    assert "SENALES MEDIDAS" in prompt
    assert "mrz.checks_ok" in prompt


def test_el_prompt_distingue_contradiccion_de_zona_gris():
    """Fija el arreglo que salio de la primera medicion sobre calibracion.

    La version anterior definia escalar como "la evidencia es contradictoria
    o esta en zona gris", y el agente escalaba los dos fraudes porque son
    literalmente contradicciones entre el anverso y la MRZ. Obedecia la
    instruccion. Si alguien vuelve a meter esa frase, este test lo para.
    """
    instrucciones = sin_saltos(INSTRUCTIONS)

    assert "Una contradiccion NO es motivo para escalar" in instrucciones
    assert "evidencia de manipulacion, no una duda sobre ella" in instrucciones


def test_el_prompt_abre_sitio_a_lo_que_no_es_identidad():
    """El otro fallo de la primera medicion: aprobo a un menor de edad.

    El menu estaba redactado entero en terminos de identidad y autenticidad,
    asi que un documento autentico de alguien cuya solicitud no le
    corresponde resolver a este sistema no tenia casilla donde caer.
    """
    instrucciones = sin_saltos(INSTRUCTIONS)

    assert "no esta en posicion de resolver" in instrucciones
    assert "no son la misma pregunta" in instrucciones


def test_el_prompt_explica_respecto_a_que_se_mide_el_peso():
    """Sin esto, el contrato tumbaba decisiones correctas.

    En la primera tanda con el menu reescrito, el agente acerto el caso del
    menor de edad -- documento autentico, titular menor, revision humana --
    y fundamento el escalado con la edad marcada como `in_favor`, porque
    tener 16 anos es un hecho normal de un documento valido. El validador
    exige un fundamento en contra o no concluyente para no aprobar, asi que
    tiro la respuesta entera y la solicitud acabo escalada por fallback, sin
    que el razonamiento llegara a nadie.

    El fallo era del prompt: explicaba que citar y que se verifica, pero no
    respecto a que se mide el peso.
    """
    instrucciones = sin_saltos(INSTRUCTIONS)

    assert "respecto a aprobar" in instrucciones
    assert "no respecto a si el documento esta bien" in instrucciones


@pytest.mark.parametrize("peso", list(Weight))
def test_el_prompt_explica_cada_peso_posible(peso):
    """Los tres valores tienen que estar explicados, no solo nombrados.

    El esquema de respuesta ya obliga al modelo a elegir uno de los tres; lo
    que el prompt aporta es que signifiquen algo.
    """
    assert peso.value in sin_saltos(INSTRUCTIONS)
