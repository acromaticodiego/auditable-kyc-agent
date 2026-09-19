"""Pruebas del camino en vivo: subir un documento, medirlo y decidir.

Es el camino que se usa delante de alguien, y por eso el reparto de quien
puede gastar cupo importa mas aqui que en ningun otro sitio: **medir tiene
que ser gratis y repetible**, porque es lo que se pulsa varias veces
mientras se explica, y **decidir tiene que ser el unico que gaste**, porque
es lo que hay que poder autorizar a conciencia.

Como en el resto de la pantalla, el cliente que se inyecta lleva un
transporte que revienta si alguien lo usa. Uno que devolviera una respuesta
valida haria pasar los tests igual sin probar la promesa.
"""

import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.budget import RequestBudget
from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.api.demo import _MEDICIONES, get_demo_client
from app.evaluation.catalog import load_cases
from app.main import app

MODELO = "modelo-de-prueba"

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


def _png(imagen) -> bytes:
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def documento():
    """Un anverso y un reverso de verdad, de un caso del catalogo."""
    caso = next(c for c in load_cases() if c.id == "legitimo-torcido")
    anverso, reverso = caso.build()
    return _png(anverso), _png(reverso)


def _explota(request: httpx.Request) -> httpx.Response:
    raise AssertionError("se llamo a la API de Gemini y no debia llamarse")


def _cliente(tmp_path, transporte=None) -> GeminiClient:
    return GeminiClient(
        api_key="clave-de-prueba",
        model=MODELO,
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=20),
        http_client=httpx.Client(
            transport=httpx.MockTransport(transporte or _explota)
        ),
    )


@pytest.fixture
def api(tmp_path):
    hechos = {}

    def _construir(transporte=None) -> TestClient:
        modelo = _cliente(tmp_path, transporte)
        hechos["modelo"] = modelo
        app.dependency_overrides[get_demo_client] = lambda: modelo
        return TestClient(app)

    _construir.hechos = hechos
    yield _construir
    app.dependency_overrides.clear()
    _MEDICIONES.clear()


def _subir(cliente: TestClient, documento) -> dict:
    anverso, reverso = documento
    respuesta = cliente.post(
        "/demo/medir",
        files={
            "anverso": ("anverso.png", anverso, "image/png"),
            "reverso": ("reverso.png", reverso, "image/png"),
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def test_medir_un_documento_subido_no_gasta_ni_una_peticion(api, documento):
    """La mitad gratis, que es la que se ensena en vivo.

    El OCR, los digitos de control, los cotejos y la nitidez son codigo
    normal. Si medir empezara a llamar al modelo, una demo de cinco
    minutos se comeria el cupo del dia y nadie se enteraria hasta la
    siguiente tanda de evaluacion.
    """
    cliente = api()

    cuerpo = _subir(cliente, documento)

    assert cuerpo["senales_totales"] == 28
    assert cuerpo["con_selfie"] is False
    assert cuerpo["linea_base"]["decision"] == "approve"
    assert api.hechos["modelo"].budget.spent(MODELO) == 0


def test_medir_dice_de_antemano_si_preguntar_costara_una_peticion(api, documento):
    """La pantalla no puede ofrecer un boton sin decir lo que vale."""
    cliente = api()

    cuerpo = _subir(cliente, documento)

    assert cuerpo["en_cache"] is False
    assert cuerpo["cupo_restante"] == 20


def test_decidir_sobre_una_medicion_que_ya_no_esta_no_revienta(api):
    cliente = api()

    respuesta = cliente.post("/demo/decidir", data={"ficha": "noexiste"})

    assert respuesta.status_code == 404
    assert "medir no cuesta nada" in respuesta.json()["detail"]


def test_decidir_sirve_de_cache_cuando_esas_senales_ya_se_preguntaron(api, documento):
    """Preguntar dos veces por el mismo documento cuesta una sola peticion."""
    cliente = api()
    cuerpo = _subir(cliente, documento)

    # Se siembra la respuesta para EXACTAMENTE las senales que midio el
    # endpoint. No sirve reutilizar las del catalogo: un documento subido
    # se evalua contra la fecha de hoy y el catalogo contra una fija, asi
    # que los dos juegos de senales no coinciden.
    senales = _MEDICIONES[cuerpo["ficha"]]
    sembrador = _cliente(
        api.hechos["modelo"].cache.directory,
        lambda r: httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(DECISION)}]}}
                ]
            },
        ),
    )
    sembrador.generate_json(build_prompt(senales), DECISION_RESPONSE_SCHEMA)
    sembrador.close()

    respuesta = cliente.post("/demo/decidir", data={"ficha": cuerpo["ficha"]})

    assert respuesta.status_code == 200
    d = respuesta.json()
    assert d["decidio_el_agente"] is True
    assert d["desde_cache"] is True
    assert d["decision"] == "approve"
    assert d["fundamentos"][0]["verificada"] is True


def test_si_el_proveedor_no_contesta_el_sistema_sigue_decidiendo(api, documento):
    """El momento mas probable de una demo en vivo, y no puede quedar en un error.

    Un 503 de Gemini es frecuente, y la respuesta correcta no es una
    pantalla rota: es escalar a revision humana. Ni aprobar -seria aprobar
    sin haber leido la evidencia- ni rechazar, que acusaria a una persona
    de suplantacion porque se cayo una API. Ver docs/adr/0004.
    """
    cliente = api(lambda r: httpx.Response(503, text="sobrecargado"))
    cuerpo = _subir(cliente, documento)

    respuesta = cliente.post("/demo/decidir", data={"ficha": cuerpo["ficha"]})

    assert respuesta.status_code == 200
    d = respuesta.json()
    assert d["decidio_el_agente"] is False
    assert d["decision"] == "escalate_to_human"
    assert d["motivo"] == "unavailable"


def test_se_puede_descargar_el_documento_de_un_caso_para_retocarlo(api):
    """Sin esto no hay demostracion en vivo: hay que traer una cedula de algun sitio.

    Las reales no pueden entrar al repositorio, asi que el unico documento
    disponible para retocar a mano y volver a subir es uno sintetico
    servido por la propia pantalla.
    """
    cliente = api()

    respuesta = cliente.get("/demo/casos/legitimo-torcido/imagen/anverso")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "image/png"
    assert respuesta.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_no_se_pueden_descargar_las_imagenes_del_reservado(api):
    cliente = api()

    respuesta = cliente.get("/demo/casos/legitimo-limpio/imagen/anverso")

    assert respuesta.status_code == 404
