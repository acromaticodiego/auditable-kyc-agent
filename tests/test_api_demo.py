"""Pruebas de la pantalla.

Lo que se prueba aqui no es que la pagina se vea bien, sino la unica
promesa que puede romperse en silencio y costar dinero: **la pantalla no
pide nada al modelo**.  Una demo se pulsa muchas veces delante de alguien y
el cupo es de veinte peticiones al dia; si un dia deja de servir solo desde
cache, no lo dira ningun error, lo dira el contador al dia siguiente.

Por eso el cliente que se le inyecta lleva un transporte que **revienta si
alguien lo usa**.  Un transporte que devolviera una respuesta valida haria
pasar los tests igual y no probaria nada.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.budget import RequestBudget
from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.api.demo import get_demo_client
from app.evaluation.catalog import TODAY, load_cases
from app.main import app
from app.signals.pipeline import build_signals

CASO = "legitimo-torcido"

DECISION = {
    "decision": "approve",
    "summary": "Todos los cotejos coinciden y el documento sigue vigente.",
    "groundings": [
        {
            "signal_id": "cross.nuip",
            "cited_value": "match",
            "weight": "in_favor",
            "text": "El NUIP del anverso coincide con el de la MRZ.",
        }
    ],
}

# Una decision con una cita falsa: dice que el cotejo del NUIP salio
# 'mismatch' cuando de verdad salio 'match'.  Sirve para comprobar que la
# columna de contraste mira la auditoria y no se limita a repetir lo que
# el modelo afirmo, que es justo lo que distingue a esta pantalla.
DECISION_CON_CITA_FALSA = {
    "decision": "reject",
    "summary": "Se rechaza porque el NUIP no coincide entre anverso y MRZ.",
    "groundings": [
        {
            "signal_id": "cross.nuip",
            "cited_value": "mismatch",
            "weight": "against",
            "text": "El NUIP del anverso no coincide con el de la MRZ.",
        }
    ],
}


def _senales_de(caso_id: str):
    caso = next(c for c in load_cases() if c.id == caso_id)
    return build_signals(*caso.build(), today=TODAY)


def _explota(request: httpx.Request) -> httpx.Response:
    raise AssertionError(
        "la pantalla intento llamar a la API de Gemini. Solo puede servir "
        "respuestas que ya esten en cache."
    )


def _cliente(tmp_path, *, sembrar: dict | None = None) -> GeminiClient:
    """Un cliente que no puede pedir nada, con la cache sembrada a mano."""
    cache = ResponseCache(tmp_path)
    if sembrar is not None:
        # Se siembra con un cliente aparte y transporte normal, y el que se
        # devuelve ya no puede pedir: asi el test no depende de la cache
        # real del disco ni la ensucia.
        import json

        sembrador = GeminiClient(
            api_key="clave-de-prueba",
            model="modelo-de-prueba",
            cache=cache,
            budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        200,
                        json={
                            "candidates": [
                                {"content": {"parts": [{"text": json.dumps(sembrar)}]}}
                            ]
                        },
                    )
                )
            ),
        )
        sembrador.generate_json(
            build_prompt(_senales_de(CASO)), DECISION_RESPONSE_SCHEMA
        )
        sembrador.close()

    return GeminiClient(
        api_key="clave-de-prueba",
        model="modelo-de-prueba",
        cache=cache,
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=20),
        http_client=httpx.Client(transport=httpx.MockTransport(_explota)),
    )


@pytest.fixture
def api(tmp_path):
    def _construir(*, sembrar: dict | None = None) -> tuple[TestClient, GeminiClient]:
        modelo = _cliente(tmp_path, sembrar=sembrar)
        app.dependency_overrides[get_demo_client] = lambda: modelo
        return TestClient(app), modelo

    yield _construir
    app.dependency_overrides.clear()


def test_un_caso_cacheado_se_sirve_sin_tocar_la_api(api):
    cliente, _ = api(sembrar=DECISION)

    respuesta = cliente.get(f"/demo/casos/{CASO}")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["decision"] == "approve"
    assert cuerpo["fundamentos"][0]["verificada"] is True


def test_un_caso_sin_cachear_se_niega_en_vez_de_pedirlo(api):
    """La promesa entera de la pantalla.

    Sin la comprobacion de cache en el router, esto llamaria a la API de
    verdad. El transporte revienta, asi que si la puerta desaparece el test
    no falla por un 409 que no llego: falla por la llamada.
    """
    cliente, modelo = api()

    respuesta = cliente.get(f"/demo/casos/{CASO}")

    assert respuesta.status_code == 409
    assert "no esta en cache" in respuesta.json()["detail"]
    assert modelo.budget.spent("modelo-de-prueba") == 0


def test_la_pantalla_no_da_acceso_al_conjunto_reservado(api):
    """Una pantalla que invita a pulsar es la forma mas facil de quemarlo.

    El reservado tambien tiene casos en cache, asi que servirlos no
    costaria nada y por eso el limite tiene que ser explicito: lo que
    protege la particion no es el cupo, es no haber mirado.
    """
    cliente, _ = api()

    respuesta = cliente.get("/demo/casos/legitimo-limpio")

    assert respuesta.status_code == 404


def test_la_columna_de_contraste_delata_una_cita_falsa(api):
    """Enseñar lo que el modelo dijo es facil; lo que vale es si era verdad.

    Si la pantalla se limitara a repetir el fundamento, esta decision -que
    afirma un 'mismatch' donde hubo un 'match'- se veria igual de creible
    que una correcta.
    """
    cliente, _ = api(sembrar=DECISION_CON_CITA_FALSA)

    cuerpo = cliente.get(f"/demo/casos/{CASO}").json()

    fundamento = cuerpo["fundamentos"][0]
    assert fundamento["verificada"] is False
    assert fundamento["estado"] == "value_mismatch"
    assert fundamento["valor_citado"] == "mismatch"
    assert fundamento["valor_real"] == "match"
    assert cuerpo["auditoria"]["fiel"] is False


def test_la_lista_solo_trae_casos_de_calibracion(api):
    cliente, _ = api()

    cuerpo = cliente.get("/demo/casos").json()

    ids = {c["id"] for c in cuerpo["casos"]}
    assert ids == {c.id for c in load_cases()}
    assert "legitimo-limpio" not in ids


def test_la_pareja_dice_en_que_sentido_va_el_salto(api):
    """El boton no puede decir 'romper' cuando el salto es de vuelta."""
    cliente, _ = api(sembrar=DECISION)

    cuerpo = cliente.get(f"/demo/casos/{CASO}").json()

    assert cuerpo["pareja"]["caso"] == "ambiguo-apellido-difiere-una-letra"
    assert cuerpo["pareja"]["sentido"] == "romper"


def test_la_linea_base_se_calcula_aparte_y_no_copia_al_agente(api):
    """La comparacion que sostiene el proyecto: 12/13 del agente contra 10/13.

    Se siembra al agente una decision de RECHAZO sobre un documento limpio,
    que es lo contrario de lo que las reglas fijas dicen ante esas mismas
    senales. Si el campo `linea_base` se limitara a repetir la decision del
    agente -o a leerse del mismo sitio- las dos saldrian iguales y este test
    lo veria. Es la unica forma de distinguir "se calculo" de "se copio".
    """
    cliente, _ = api(sembrar=DECISION_CON_CITA_FALSA)

    cuerpo = cliente.get(f"/demo/casos/{CASO}").json()

    assert cuerpo["decision"] == "reject"
    assert cuerpo["linea_base"]["decision"] == "approve"
    assert cuerpo["linea_base"]["acierta"] is True
    assert cuerpo["acierta"] is False
