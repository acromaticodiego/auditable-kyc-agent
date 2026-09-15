"""Pruebas del resumen de auditoria, contra el Postgres de verdad.

Lo que se comprueba aqui es que el recuento sepa distinguir las dos cosas
que es tentador mezclar: un escalado que el agente decidio y un escalado que
ocurrio porque el agente no llego a decidir nada.  En produccion las dos
acaban en la misma cola; en el informe no pueden contar igual, porque la
segunda mide la salud del proveedor y la primera el juicio del modelo.

Las comprobaciones miden **incrementos y no totales**.  La primera version
miraba los totales y funcionaba solo porque este fichero corre antes que los
demas por orden alfabetico y se encontraba la tabla vacia.  Bastaba una fila
suelta -- las que deja un test que falla antes de registrar su identificador
para limpieza -- para tumbar cuatro de las cinco pruebas, y ademas con
errores que parecen un fallo del resumen en vez de una fila vieja.
"""

import io
import json
import uuid
from datetime import date

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image

from app.agent.budget import RequestBudget
from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.api.verifications import get_agent_client
from app.db import engine
from app.main import app
from app.storage.models import ensure_schema, verificaciones
from app.synthetic.cedula import CedulaData, render_back, render_front
from app.synthetic.tampering import retouch_front

APROBACION = {
    "decision": "approve",
    "summary": "El documento es coherente consigo mismo y la imagen es legible.",
    "groundings": [
        {
            "signal_id": "mrz.checks_ok",
            "cited_value": "True",
            "weight": "in_favor",
            "text": "Los digitos de control de la MRZ cuadran todos.",
        }
    ],
}


def persona() -> CedulaData:
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


def png(imagen: Image.Image) -> bytes:
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module", autouse=True)
def esquema():
    ensure_schema(engine)


@pytest.fixture
def antes():
    """El resumen tal y como estaba antes de que esta prueba escriba nada."""
    from app.storage.verifications import resumen

    return resumen(engine)


def crecio(despues: dict, antes: dict, clave: str) -> int:
    return despues[clave] - antes[clave]


def crecio_en(despues: dict, antes: dict, clave: str, subclave: str) -> int:
    return despues[clave].get(subclave, 0) - antes[clave].get(subclave, 0)


def calladas(resumen: dict) -> dict[str, int]:
    return {
        fila["signal_id"]: fila["veces"]
        for fila in resumen["senales_adversas_mas_calladas"]
    }


@pytest.fixture
def creadas():
    ids: list[uuid.UUID] = []
    yield ids
    if ids:
        with engine.begin() as conexion:
            conexion.execute(
                sa.delete(verificaciones).where(verificaciones.c.id.in_(ids))
            )


@pytest.fixture(autouse=True)
def sin_dependencias_pisadas():
    yield
    app.dependency_overrides.clear()


def api(respuesta: str, tmp_path, *, status: int = 200) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=respuesta)
        cuerpo = {"candidates": [{"content": {"parts": [{"text": respuesta}]}}]}
        return httpx.Response(200, json=cuerpo)

    modelo = GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-test",
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    app.dependency_overrides[get_agent_client] = lambda: modelo
    return TestClient(app)


def limpio() -> tuple[Image.Image, Image.Image]:
    datos = persona()
    return render_front(datos), render_back(datos)


def con_apellido_retocado() -> tuple[Image.Image, Image.Image]:
    datos = persona()
    retocados, mrz_original = retouch_front(datos, surnames="WALTEROZ")
    return render_front(retocados), render_back(datos, mrz_lines=mrz_original)


def subir(cliente: TestClient, par) -> dict:
    anverso, reverso = par
    return cliente.post(
        "/verificaciones",
        files={
            "anverso": ("anverso.png", png(anverso), "image/png"),
            "reverso": ("reverso.png", png(reverso), "image/png"),
        },
    ).json()


def test_el_resumen_separa_el_escalado_decidido_del_escalado_por_fallo(
    tmp_path, creadas, antes
):
    """El numero incomodo del ADR-0004, y el motivo de que exista el resumen.

    Dos verificaciones acaban en revision humana. Una porque el agente lo
    decidio mirando las senales; la otra porque el proveedor no respondio y
    el sistema escalo por defecto. En la cola del analista son iguales. En
    este informe no pueden serlo: la segunda no dice nada sobre el juicio
    del modelo, dice que Google estaba caido.
    """
    escalada = {
        "decision": "escalate_to_human",
        "summary": "La discrepancia del apellido necesita que la mire alguien.",
        "groundings": [
            {
                "signal_id": "cross.surnames",
                "cited_value": "mismatch",
                "weight": "against",
                "text": "El apellido del anverso no coincide con el de la MRZ.",
            }
        ],
    }
    cliente = api(json.dumps(escalada), tmp_path)
    creadas.append(uuid.UUID(subir(cliente, con_apellido_retocado())["id"]))

    # DOS decididas y UNA sin decidir, a proposito.
    #
    # Con una de cada, este test no valia para nada: contar las que SI
    # decidieron da el mismo numero que contar las que no, asi que invertir
    # la condicion de la consulta no cambiaba el resultado y la prueba
    # pasaba igual. Lo destapo mutar el codigo. Con dos y una, los dos
    # numeros se separan y la comprobacion empieza a comprobar algo.
    aprueba = api(json.dumps(APROBACION), tmp_path / "aprueba")
    creadas.append(uuid.UUID(subir(aprueba, limpio())["id"]))

    caido = api("sobrecargado", tmp_path / "otro", status=503)
    creadas.append(uuid.UUID(subir(caido, limpio())["id"]))

    despues = caido.get("/auditoria/resumen").json()

    assert crecio(despues, antes, "verificaciones") == 3
    assert crecio_en(despues, antes, "por_decision", "escalate_to_human") == 2
    assert crecio_en(despues, antes, "por_decision", "approve") == 1
    # Dos escalaron, pero solo una de ellas fue un juicio del agente.
    assert crecio(despues, antes, "escalados_sin_juicio_del_agente") == 1
    assert crecio(despues, antes, "con_explicacion") == 2


def test_la_fidelidad_solo_se_cuenta_sobre_las_que_tuvieron_explicacion(
    tmp_path, creadas, antes
):
    """Meter en el denominador las que nunca la tuvieron mediria otra cosa.

    Concretamente mediria la salud de la infraestructura de Google y la
    llamaria calidad del agente.
    """
    cliente = api(json.dumps(APROBACION), tmp_path)
    creadas.append(uuid.UUID(subir(cliente, limpio())["id"]))

    caido = api("sobrecargado", tmp_path / "otro", status=503)
    for _ in range(3):
        creadas.append(uuid.UUID(subir(caido, limpio())["id"]))

    despues = caido.get("/auditoria/resumen").json()

    assert crecio(despues, antes, "verificaciones") == 4
    assert crecio(despues, antes, "con_explicacion") == 1
    # Una de una, no una de cuatro.
    assert crecio(despues, antes, "explicaciones_fieles") == 1
    assert crecio(despues, antes, "explicaciones_completas") == 1


def test_el_resumen_dice_que_senales_se_calla_el_agente(tmp_path, creadas, antes):
    """La consulta que justifica haber guardado las omisiones por fila.

    Con una lista de texto en la cabecera esto seria un LIKE sobre una
    cadena; aqui es un GROUP BY.
    """
    # Aprueba citando solo que la MRZ cuadra, callandose la discrepancia.
    cliente = api(json.dumps(APROBACION), tmp_path)
    for _ in range(2):
        creadas.append(uuid.UUID(subir(cliente, con_apellido_retocado())["id"]))

    # Y una tercera con la MISMA senal adversa pero CITADA.
    #
    # Sin ella el test no comprobaba nada: en las dos de arriba la senal es
    # adversa y ademas callada, asi que contar las adversas daba el mismo
    # numero que contar las calladas y la consulta podia mirar la columna
    # equivocada sin que nadie lo notara. Lo destapo mutar el codigo.
    reconoce = {
        "decision": "escalate_to_human",
        "summary": "La discrepancia del apellido necesita que la mire alguien.",
        "groundings": [
            {
                "signal_id": "cross.surnames",
                "cited_value": "mismatch",
                "weight": "against",
                "text": "El apellido del anverso no coincide con el de la MRZ.",
            }
        ],
    }
    honesto = api(json.dumps(reconoce), tmp_path / "honesto")
    creadas.append(uuid.UUID(subir(honesto, con_apellido_retocado())["id"]))

    despues = honesto.get("/auditoria/resumen").json()

    antes_calladas = calladas(antes)
    # Tres verificaciones con la senal adversa, pero solo dos se la callaron.
    assert calladas(despues)["cross.surnames"] - antes_calladas.get(
        "cross.surnames", 0
    ) == 2
    assert crecio(despues, antes, "explicaciones_fieles") == 3
    # Solo la tercera es completa: las otras dos citan la verdad y se callan
    # la discrepancia.
    assert crecio(despues, antes, "explicaciones_completas") == 1


def test_las_cuentas_del_resumen_son_coherentes_entre_si(tmp_path):
    """Invariantes que se cumplen con la tabla vacia y con la tabla llena.

    Sustituye a una prueba que exigia la tabla vacia y que por tanto solo
    funcionaba si nadie habia escrito antes. Lo que de verdad hay que fijar
    no es que haya cero filas, sino que ningun sumando pueda superar a su
    propio total ni salir negativo: ahi es donde un resumen empieza a
    inventarse cifras.
    """
    cliente = api(json.dumps(APROBACION), tmp_path)

    resumen = cliente.get("/auditoria/resumen").json()
    total = resumen["verificaciones"]

    assert resumen["con_explicacion"] <= total
    assert resumen["escalados_sin_juicio_del_agente"] <= total
    assert resumen["explicaciones_fieles"] <= resumen["con_explicacion"]
    assert resumen["explicaciones_completas"] <= resumen["con_explicacion"]
    assert sum(resumen["por_decision"].values()) == total
    assert sum(resumen["por_resultado_del_agente"].values()) == total
    assert all(veces > 0 for veces in calladas(resumen).values())


def test_el_resumen_devuelve_recuentos_y_no_porcentajes(tmp_path, creadas, antes):
    """Sobre cinco verificaciones un porcentaje no mide nada.

    Y quien lo lea de reojo lo tratara como si midiera, asi que el
    denominador va siempre delante y no se calcula ninguna tasa aqui.
    """
    cliente = api(json.dumps(APROBACION), tmp_path)
    creadas.append(uuid.UUID(subir(cliente, limpio())["id"]))

    resumen = cliente.get("/auditoria/resumen").json()

    numericos = [
        valor for clave, valor in resumen.items() if isinstance(valor, (int, float))
    ]
    assert numericos, "el resumen no trae ninguna cifra"
    for valor in numericos:
        assert isinstance(valor, int), "hay una cifra no entera: parece una tasa"
    assert "advertencia" in resumen
