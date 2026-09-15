"""Pruebas de la auditoria de lo que la explicacion se callo.

El caso que da sentido al modulo es el primero: una decision cuyas citas
son todas verdaderas y que aun asi esconde lo unico que importaba.
"""

import pytest

from app.domain.completeness import (
    MISMATCH,
    CompletenessReport,
    adverse_signals,
    audit_completeness,
)
from app.domain.citation_audit import audit_citations
from app.domain.decision import AgentDecision, DecisionKind, Grounding, Weight
from app.domain.signals import Signal, SignalKind, SignalSet
from app.signals.cross_check import CrossStatus


def conjunto(**cambios) -> SignalSet:
    valores = {
        "cross.surnames": "match",
        "cross.nuip": "match",
        "mrz.checks_ok": True,
        "mrz.readable": True,
        "document.expired": False,
        "document.dates_coherent": True,
    }
    valores.update(cambios)
    senales = []
    for identificador, valor in valores.items():
        tipo = SignalKind.TEXT if identificador.startswith("cross.") else SignalKind.FLAG
        senales.append(
            Signal(identificador, tipo, f"Senal {identificador}.", value=valor)
        )
    return SignalSet(senales)


def decision(*citadas: str, kind=DecisionKind.APPROVE) -> AgentDecision:
    peso = Weight.IN_FAVOR if kind is DecisionKind.APPROVE else Weight.AGAINST
    return AgentDecision(
        decision=kind,
        summary="Un resumen cualquiera con longitud suficiente.",
        groundings=[
            Grounding(
                signal_id=identificador,
                cited_value="lo que sea",
                weight=peso,
                text="Un fundamento con la longitud minima exigida.",
            )
            for identificador in citadas
        ],
    )


def test_una_explicacion_verdadera_puede_estar_incompleta():
    """El caso que justifica todo el modulo.

    El agente aprueba citando con toda exactitud que la MRZ es legible,
    callandose que el apellido del anverso no coincide con el de la MRZ. Sus
    citas son verdaderas una a una; su explicacion esconde lo unico que
    importaba.
    """
    senales = conjunto(**{"cross.surnames": MISMATCH})
    aprobacion = decision("mrz.readable")

    informe = audit_completeness(aprobacion, senales)

    assert informe.adverse == ["cross.surnames"]
    assert informe.omitted == ["cross.surnames"]
    assert not informe.complete


def test_la_fidelidad_no_detecta_esa_omision():
    """La prueba de que las dos medidas no son la misma.

    Si la fidelidad ya cubriera esto, el modulo sobraria. Aqui se comprueba
    que la MISMA decision es fiel y a la vez incompleta.
    """
    senales = conjunto(**{"cross.surnames": MISMATCH})
    aprobacion = AgentDecision(
        decision=DecisionKind.APPROVE,
        summary="Todo correcto segun lo que se ha comprobado.",
        groundings=[
            Grounding(
                signal_id="mrz.readable",
                cited_value="True",
                weight=Weight.IN_FAVOR,
                text="La MRZ del reverso se leyo sin problemas.",
            )
        ],
    )

    assert audit_citations(aprobacion, senales).faithful
    assert not audit_completeness(aprobacion, senales).complete


def test_citar_la_senal_adversa_basta_aunque_se_decida_en_contra():
    """Este modulo no juzga la decision, solo el silencio.

    Decidir a pesar de una senal adversa es legitimo -- el contrato de
    AgentDecision lo permite a proposito -- y es justo lo que se quiere
    poder leer despues. Lo que no vale es no mencionarla.
    """
    senales = conjunto(**{"cross.surnames": MISMATCH})
    aprobacion = AgentDecision(
        decision=DecisionKind.APPROVE,
        summary="Se aprueba pese a la discrepancia, por los motivos dados.",
        groundings=[
            Grounding(
                signal_id="cross.surnames", cited_value=MISMATCH,
                weight=Weight.AGAINST,
                text="El apellido no coincide, pero es una sola letra.",
            ),
            Grounding(
                signal_id="mrz.checks_ok", cited_value="True",
                weight=Weight.IN_FAVOR,
                text="Los digitos de control de la MRZ cuadran.",
            ),
        ],
    )

    assert audit_completeness(aprobacion, senales).complete


def test_sin_senales_adversas_la_explicacion_sale_completa():
    informe = audit_completeness(decision("mrz.checks_ok"), conjunto())

    assert informe.adverse == []
    assert informe.complete


@pytest.mark.parametrize(
    "cambio, esperada",
    [
        ({"cross.nuip": MISMATCH}, "cross.nuip"),
        ({"mrz.checks_ok": False}, "mrz.checks_ok"),
        ({"mrz.readable": False}, "mrz.readable"),
        ({"document.expired": True}, "document.expired"),
        ({"document.dates_coherent": False}, "document.dates_coherent"),
    ],
)
def test_cada_condicion_adversa_se_reconoce(cambio, esperada):
    """Barre las cinco condiciones, para que anadir una sin test no cuele."""
    assert adverse_signals(conjunto(**cambio)) == [esperada]


def test_una_senal_que_no_se_pudo_medir_no_cuenta_como_adversa():
    """De una medicion que no se hizo no se puede afirmar nada.

    Contarla como adversa castigaria al agente por callar algo que nadie
    sabe, y ademas inflaria el numero de omisiones en las capturas malas,
    que es justo donde hay mas senales sin medir.
    """
    senales = SignalSet(
        [
            Signal("mrz.readable", SignalKind.FLAG, "Si la MRZ se pudo leer.",
                   unavailable_reason="el recorte del reverso salio vacio"),
            Signal("mrz.checks_ok", SignalKind.FLAG, "Digitos de control.",
                   unavailable_reason="no hay MRZ que comprobar"),
        ]
    )

    assert adverse_signals(senales) == []


def test_el_valor_de_discrepancia_es_el_mismo_que_produce_el_cotejo():
    """Si el cotejo cambiara su vocabulario, este modulo dejaria de ver nada.

    Y no fallaria: `adverse_signals` devolveria una lista vacia y la
    completitud saldria perfecta en todos los casos, que es la peor forma
    posible de romperse. Por eso la coherencia entre ambos se fija aqui.
    """
    assert MISMATCH == CrossStatus.MISMATCH.value


def test_el_informe_distingue_cuantas_habia_de_cuantas_se_callaron():
    """Un conjunto sin casos adversos daria completitud perfecta sin merito.

    Informar solo el porcentaje escondria eso, asi que el informe lleva
    tambien cuantas senales adversas habia.
    """
    senales = conjunto(**{"cross.surnames": MISMATCH, "document.expired": True})
    parcial = decision("cross.surnames")

    informe = audit_completeness(parcial, senales)

    assert set(informe.adverse) == {"cross.surnames", "document.expired"}
    assert informe.omitted == ["document.expired"]
    assert isinstance(informe, CompletenessReport)
