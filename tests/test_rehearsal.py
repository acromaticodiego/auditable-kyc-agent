"""Pruebas del doble que permite ensayar una tanda sin gastar cupo.

Lo que se fija aqui es que el ensayo llegue hasta el final del arnes, que
es su unica razon de ser: un fallo del informe que solo aparece con
decisiones reales cuesta las doce peticiones del dia y la espera hasta el
siguiente.
"""

import pytest

from app.agent.runner import RunOutcome, run_agent
from app.domain.decision import AgentDecision
from app.evaluation.baseline import decide as decide_baseline
from app.evaluation.rehearsal import MODELO_DE_ENSAYO, ModeloDeEnsayo
from app.domain.signals import Signal, SignalKind, SignalSet


def conjunto(**cambios) -> SignalSet:
    valores = {
        "mrz.readable": True,
        "mrz.checks_ok": True,
        "quality.front_sharpness": 1505,
        "quality.front_glare": 0.1,
        "ocr.fields_missing": 0,
        "document.expired": False,
        "cross.nuip": "match",
    }
    valores.update(cambios)
    senales = []
    for identificador, valor in valores.items():
        if isinstance(valor, bool):
            tipo = SignalKind.FLAG
        elif isinstance(valor, str):
            tipo = SignalKind.TEXT
        elif isinstance(valor, float):
            tipo = SignalKind.SCORE
        else:
            tipo = SignalKind.COUNT
        senales.append(
            Signal(identificador, tipo, f"Senal {identificador}.", value=valor)
        )
    return SignalSet(senales)


def test_el_ensayo_produce_una_decision_que_el_contrato_acepta():
    """Es lo unico que hace falta: que el bucle llegue hasta el final.

    Si el doble devolviera algo que `AgentDecision` rechaza, el ensayo
    terminaria en contract_violation y no habria recorrido el informe, que
    es justo lo que se quiere ensayar.
    """
    senales = conjunto()
    modelo = ModeloDeEnsayo({"unico": senales})

    run = run_agent(senales, modelo)

    assert run.outcome is RunOutcome.DECIDED
    assert isinstance(run.decision, AgentDecision)
    assert run.audit is not None
    assert run.completeness is not None


def test_el_ensayo_responde_lo_mismo_que_la_linea_base():
    """Se eligio asi para que las decisiones salgan variadas y validas.

    Una respuesta fija recorreria una sola rama del informe y dejaria sin
    ensayar las demas, que es donde suelen estar los fallos de formato.
    """
    for cambio in (
        {},
        {"mrz.checks_ok": False},
        {"mrz.readable": False},
        {"document.expired": True},
        {"ocr.fields_missing": 2},
    ):
        senales = conjunto(**cambio)
        modelo = ModeloDeEnsayo({"unico": senales})

        run = run_agent(senales, modelo)

        assert run.decision.decision is decide_baseline(senales).decision


def test_el_ensayo_no_gasta_nada():
    senales = conjunto()
    modelo = ModeloDeEnsayo({"unico": senales})

    assert modelo.budget.remaining(MODELO_DE_ENSAYO) is None
    assert modelo.budget.spent(MODELO_DE_ENSAYO) == 0
    run_agent(senales, modelo)
    assert modelo.budget.spent(MODELO_DE_ENSAYO) == 0


def test_el_ensayo_no_puede_hablar_con_la_red():
    """Estructuralmente, no por disciplina: no tiene cliente HTTP ni clave."""
    modelo = ModeloDeEnsayo({"unico": conjunto()})

    assert not hasattr(modelo, "_http")
    assert not hasattr(modelo, "api_key")


def test_un_prompt_no_registrado_revienta_en_vez_de_inventarse_algo():
    """Es un fallo del arnes y disimularlo daria un ensayo que no ensaya.

    El caso real que esto detecta es calcular las senales dos veces con
    parametros distintos -- por ejemplo con fechas distintas -- de modo que
    el prompt que llega no es el que se registro.
    """
    modelo = ModeloDeEnsayo({"unico": conjunto()})

    with pytest.raises(KeyError, match="no tenia registrado"):
        modelo.generate_json("un prompt que nadie registro", {})


def test_el_modelo_de_ensayo_se_identifica_como_tal():
    """Para que su resultado no pueda confundirse con una medicion."""
    modelo = ModeloDeEnsayo({"unico": conjunto()})

    assert modelo.model == MODELO_DE_ENSAYO
    assert "ensayo" in modelo.model
