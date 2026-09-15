"""Pruebas de lo que permite contar el coste de una tanda antes de lanzarla.

Sin esto, una evaluacion de doce casos con tres peticiones de cupo se
descubre a medias: se gastan las tres, se aborta y los nueve restantes
quedan sin medir hasta el dia siguiente.
"""

import json

import httpx

from app.agent.budget import RequestBudget
from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient

ESQUEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def cliente(tmp_path, peticiones: list) -> GeminiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        cuerpo = {"candidates": [{"content": {"parts": [{"text": '{"a": "b"}'}]}}]}
        return httpx.Response(200, json=cuerpo)

    return GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-test",
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_preguntar_si_algo_esta_en_cache_no_gasta_una_peticion(tmp_path):
    """Contar el coste no puede ser parte del coste."""
    peticiones: list[httpx.Request] = []
    modelo = cliente(tmp_path, peticiones)

    for _ in range(5):
        modelo.is_cached("un prompt cualquiera", ESQUEMA)

    assert peticiones == []


def test_lo_no_pedido_no_esta_en_cache_y_lo_pedido_si(tmp_path):
    peticiones: list[httpx.Request] = []
    modelo = cliente(tmp_path, peticiones)

    assert not modelo.is_cached("prompt uno", ESQUEMA)
    modelo.generate_json("prompt uno", ESQUEMA)

    assert modelo.is_cached("prompt uno", ESQUEMA)
    # Un prompt distinto es una peticion distinta: si `is_cached` respondiera
    # que si, la tanda creeria que no gasta nada y gastaria el cupo entero.
    assert not modelo.is_cached("prompt dos", ESQUEMA)
    assert len(peticiones) == 1


def test_el_recuento_de_cache_va_por_modelo(tmp_path):
    """Dos modelos ante el mismo prompt son dos sistemas distintos.

    Si la respuesta de uno contara como cache del otro, cambiar de modelo
    para estirar el cupo devolveria las decisiones del modelo anterior y la
    comparacion entre ambos no mediria nada.
    """
    peticiones: list[httpx.Request] = []
    uno = cliente(tmp_path, peticiones)
    uno.generate_json("el mismo prompt", ESQUEMA)

    otro = GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-otro",
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ),
    )

    assert uno.is_cached("el mismo prompt", ESQUEMA)
    assert not otro.is_cached("el mismo prompt", ESQUEMA)


def test_una_entrada_corrupta_no_cuenta_como_cache(tmp_path):
    """Si contara, la tanda creeria que ese caso es gratis y se quedaria sin el.

    El coste de equivocarse aqui es asimetrico: dar por cacheado algo que no
    lo esta rompe el recuento de la tanda; darlo por no cacheado solo gasta
    una peticion de mas.
    """
    peticiones: list[httpx.Request] = []
    modelo = cliente(tmp_path, peticiones)
    modelo.generate_json("prompt uno", ESQUEMA)

    guardadas = [p for p in tmp_path.glob("*.json") if p.name != "budget.json"]
    assert len(guardadas) == 1
    guardadas[0].write_text("{esto no es json", encoding="utf-8")

    assert not modelo.is_cached("prompt uno", ESQUEMA)


def test_el_esquema_forma_parte_de_la_identidad_de_la_peticion(tmp_path):
    """Pedir la misma pregunta con otro contrato de respuesta es otra peticion."""
    peticiones: list[httpx.Request] = []
    modelo = cliente(tmp_path, peticiones)
    modelo.generate_json("el mismo prompt", ESQUEMA)

    otro_esquema = json.loads(json.dumps(ESQUEMA))
    otro_esquema["properties"]["b"] = {"type": "string"}

    assert modelo.is_cached("el mismo prompt", ESQUEMA)
    assert not modelo.is_cached("el mismo prompt", otro_esquema)
