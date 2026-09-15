"""Pruebas del endpoint de verificacion, contra el Postgres de verdad.

La base de datos no se simula: lo que hay que comprobar aqui es que el
registro auditable queda escrito, y un doble en memoria comprobaria que el
doble funciona.  El que si se simula es el modelo, porque el cupo son 20
peticiones al dia y una suite que las gaste deja de poder ejecutarse.

Cada prueba borra las filas que ella misma creo, por su identificador.  No
se vacian las tablas: un `DELETE FROM` sin condicion en una suite es una
bomba esperando a que alguien apunte la variable de entorno a otro sitio.
"""

import io
import json
import uuid

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
from app.storage.models import (
    ensure_schema,
    verificacion_fundamentos,
    verificacion_senales,
    verificaciones,
)
from app.synthetic.cedula import CedulaData, render_back, render_front

from datetime import date

RESPUESTA_APROBACION = {
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
def creadas():
    """Recoge los identificadores creados y los borra al terminar."""
    ids: list[uuid.UUID] = []
    yield ids
    if ids:
        with engine.begin() as conexion:
            conexion.execute(
                sa.delete(verificaciones).where(verificaciones.c.id.in_(ids))
            )


def cliente_falso(tmp_path, respuesta: str, *, status: int = 200) -> GeminiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text=respuesta)
        cuerpo = {"candidates": [{"content": {"parts": [{"text": respuesta}]}}]}
        return httpx.Response(200, json=cuerpo)

    return GeminiClient(
        api_key="clave-de-prueba",
        model="gemini-test",
        cache=ResponseCache(tmp_path),
        budget=RequestBudget(tmp_path / "budget.json", daily_limit=None),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


def api(modelo: GeminiClient) -> TestClient:
    app.dependency_overrides[get_agent_client] = lambda: modelo
    return TestClient(app)


@pytest.fixture(autouse=True)
def sin_dependencias_pisadas():
    yield
    app.dependency_overrides.clear()


def subir(cliente: TestClient, datos: CedulaData | None = None):
    datos = datos or persona()
    return cliente.post(
        "/verificaciones",
        files={
            "anverso": ("anverso.png", png(render_front(datos)), "image/png"),
            "reverso": ("reverso.png", png(render_back(datos)), "image/png"),
        },
    )


def test_una_verificacion_devuelve_decision_con_fundamentos_auditados(
    tmp_path, creadas
):
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    respuesta = subir(cliente)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    creadas.append(uuid.UUID(cuerpo["id"]))

    assert cuerpo["decision"] == "approve"
    assert cuerpo["resultado_del_agente"] == "decided"
    # Cada fundamento llega con el veredicto de su auditoria al lado. Sin
    # esto el cliente recibiria la explicacion sin saber si se sostiene, que
    # es justo lo que el proyecto quiere evitar.
    assert cuerpo["fundamentos"]
    for fundamento in cuerpo["fundamentos"]:
        assert fundamento["auditoria"] == "valid"
    assert cuerpo["explicacion_fiel"] is True


def test_el_registro_guarda_las_senales_que_el_agente_tenia_delante(
    tmp_path, creadas
):
    """Es lo que hace auditable el registro dentro de seis meses.

    Guardar solo la decision y sus citas dejaria la explicacion sin nada
    contra lo que contrastarla: quedaria la palabra del agente de que la
    MRZ cuadraba.
    """
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))
    creado = subir(cliente).json()
    creadas.append(uuid.UUID(creado["id"]))

    registro = cliente.get(f"/verificaciones/{creado['id']}").json()

    ids_de_senales = {senal["signal_id"] for senal in registro["senales"]}
    assert "mrz.checks_ok" in ids_de_senales
    assert len(registro["senales"]) > 20
    # Las senales que no se pudieron medir tambien se guardan, con su
    # motivo: que falte una medicion es informacion, no un hueco.
    for senal in registro["senales"]:
        assert senal["disponible"] or senal["motivo_indisponible"]


def test_la_cita_del_agente_se_puede_contrastar_con_la_senal_guardada(
    tmp_path, creadas
):
    """La prueba de que el registro sirve para auditar y no solo para contar."""
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))
    creado = subir(cliente).json()
    creadas.append(uuid.UUID(creado["id"]))

    registro = cliente.get(f"/verificaciones/{creado['id']}").json()
    senales = {senal["signal_id"]: senal for senal in registro["senales"]}

    for fundamento in registro["fundamentos"]:
        senal = senales[fundamento["signal_id"]]
        assert fundamento["valor_citado"] == senal["valor"]


def test_las_imagenes_no_se_guardan_solo_su_hash(tmp_path, creadas):
    """Un registro de auditoria no puede convertirse en un almacen de cedulas.

    Es la decision de diseno mas facil de romper sin querer -- basta con
    anadir una columna 'por si acaso' -- y la mas cara si se rompe, asi que
    tiene un test que busca los bytes de la imagen en las tres tablas.
    """
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))
    datos = persona()
    bytes_anverso = png(render_front(datos))

    respuesta = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("anverso.png", bytes_anverso, "image/png"),
            "reverso": ("reverso.png", png(render_back(datos)), "image/png"),
        },
    )
    creado = respuesta.json()
    verificacion_id = uuid.UUID(creado["id"])
    creadas.append(verificacion_id)

    import hashlib

    esperado = hashlib.sha256(bytes_anverso).hexdigest()

    with engine.connect() as conexion:
        fila = conexion.execute(
            sa.select(verificaciones).where(verificaciones.c.id == verificacion_id)
        ).mappings().one()

    assert fila["anverso_sha256"] == esperado
    assert "anverso" not in [columna.name for columna in verificaciones.columns]

    # Ninguna columna de texto de ninguna de las tres tablas contiene los
    # bytes de la imagen disfrazados.
    trozo = bytes_anverso[:64].hex()
    for tabla in (verificaciones, verificacion_senales, verificacion_fundamentos):
        with engine.connect() as conexion:
            filas = conexion.execute(
                sa.select(tabla).where(
                    getattr(tabla.c, "verificacion_id", tabla.c.id) == verificacion_id
                )
            ).mappings().all()
        volcado = json.dumps([{k: str(v) for k, v in f.items()} for f in filas])
        assert trozo not in volcado


def test_un_fallo_del_proveedor_no_se_convierte_en_un_error_http(tmp_path, creadas):
    """Gemini devuelve 503 y esta peticion responde 201 con escalate_to_human.

    Es el punto donde es facil equivocarse: propagar el 503 le diria al
    cliente que reintente, y reintentar una verificacion que ya esta en la
    cola de un analista la duplica. El sistema SI decidio; ver adr/0004.
    """
    cliente = api(cliente_falso(tmp_path, "sobrecargado", status=503))

    respuesta = subir(cliente)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    creadas.append(uuid.UUID(cuerpo["id"]))

    assert cuerpo["decision"] == "escalate_to_human"
    assert cuerpo["resultado_del_agente"] == "unavailable"
    assert cuerpo["fundamentos"] == []
    assert "503" in (cuerpo["error"] or "")


def test_el_fallo_del_proveedor_queda_registrado_y_no_se_pierde(tmp_path, creadas):
    """Si solo se guardaran los finales felices, la tasa de escalados por
    fallo del modelo seria invisible justo en el informe que deberia
    ensenarla."""
    cliente = api(cliente_falso(tmp_path, "sobrecargado", status=503))
    creado = subir(cliente).json()
    creadas.append(uuid.UUID(creado["id"]))

    registro = cliente.get(f"/verificaciones/{creado['id']}").json()

    assert registro["resultado"] == "unavailable"
    assert registro["decision"] == "escalate_to_human"
    assert registro["explicacion_fiel"] is False
    assert registro["citas_totales"] == 0
    # Las senales SI se guardan aunque el agente no llegara a opinar: se
    # midieron, costaron su tiempo, y son lo que vera el analista humano.
    assert len(registro["senales"]) > 20


def test_una_respuesta_del_modelo_con_una_cita_falsa_queda_marcada(
    tmp_path, creadas
):
    mentira = json.loads(json.dumps(RESPUESTA_APROBACION))
    mentira["groundings"][0]["cited_value"] = "False"
    cliente = api(cliente_falso(tmp_path, json.dumps(mentira)))

    cuerpo = subir(cliente).json()
    creadas.append(uuid.UUID(cuerpo["id"]))

    assert cuerpo["explicacion_fiel"] is False
    assert cuerpo["fundamentos"][0]["auditoria"] == "value_mismatch"
    # La decision se devuelve igualmente: el auditor informa, todavia no
    # corrige. Ver la cabecera de app/agent/runner.py.
    assert cuerpo["decision"] == "approve"


def test_lo_que_no_es_una_imagen_se_rechaza_con_422(tmp_path):
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    respuesta = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("nota.txt", b"esto no es una imagen", "text/plain"),
            "reverso": ("reverso.png", png(render_back(persona())), "image/png"),
        },
    )

    assert respuesta.status_code == 422
    assert "anverso" in respuesta.json()["detail"]


def test_falta_una_de_las_dos_caras(tmp_path):
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    respuesta = cliente.post(
        "/verificaciones",
        files={"anverso": ("anverso.png", png(render_front(persona())), "image/png")},
    )

    assert respuesta.status_code == 422


def test_una_verificacion_que_no_existe_da_404(tmp_path):
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    respuesta = cliente.get(f"/verificaciones/{uuid.uuid4()}")

    assert respuesta.status_code == 404


def test_un_fichero_enorme_se_rechaza_sin_cargarlo_entero(tmp_path):
    """Leer y medir despues no defiende de nada.

    Para cuando se sabe que son 2 GB, los 2 GB ya estan en memoria. Aqui se
    envia algo por encima del tope y se espera un 413 antes de que nada lo
    intente abrir como imagen.
    """
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))
    demasiado = b"\x00" * (11 * 1024 * 1024)

    respuesta = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("grande.png", demasiado, "image/png"),
            "reverso": ("reverso.png", png(render_back(persona())), "image/png"),
        },
    )

    assert respuesta.status_code == 413
    assert "anverso" in respuesta.json()["detail"]


def test_una_imagen_bomba_se_rechaza_por_sus_dimensiones(tmp_path):
    """Pocos bytes comprimidos que se expanden a gigabytes al descomprimir.

    PIL trae un tope propio pero por debajo del doble solo AVISA, asi que
    una imagen de 100 megapixeles pasaba entera y acababa en Tesseract. Se
    rechaza mirando las dimensiones de la cabecera, antes de descomprimir.
    """
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    # 12000x12000 = 144 millones de pixeles en un PNG de un solo color, que
    # comprime a unos pocos kilobytes: pasa cualquier tope de tamano.
    bomba = io.BytesIO()
    Image.new("L", (12000, 12000), color=0).save(bomba, format="PNG")
    assert len(bomba.getvalue()) < 1024 * 1024

    respuesta = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("bomba.png", bomba.getvalue(), "image/png"),
            "reverso": ("reverso.png", png(render_back(persona())), "image/png"),
        },
    )

    assert respuesta.status_code == 413
    assert "pixeles" in respuesta.json()["detail"]


def test_una_imagen_truncada_se_rechaza_con_422_y_no_con_500(tmp_path):
    """Un PNG cortado por la mitad revienta en load(), no en open()."""
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))
    entera = png(render_front(persona()))

    respuesta = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("cortada.png", entera[: len(entera) // 2], "image/png"),
            "reverso": ("reverso.png", png(render_back(persona())), "image/png"),
        },
    )

    assert respuesta.status_code == 422
    assert "anverso" in respuesta.json()["detail"]


def test_el_tope_de_pixeles_se_mantiene_por_debajo_del_de_pil():
    """Fija la relacion que hace innecesario capturar DecompressionBombError.

    PIL solo lanza ese error por encima del DOBLE de su propio limite. Como
    el tope de este endpoint es muy inferior, la imagen se rechaza antes y
    esa excepcion no llega a ocurrir nunca. Hubo un `except` para ella y era
    codigo muerto: al borrarlo, los trece tests seguian pasando.

    Si alguien sube MAX_PIXELES por encima de ese umbral, este test falla y
    le recuerda que tiene que volver a capturarla, porque
    DecompressionBombError NO hereda de OSError y subiria como un 500.
    """
    from app.api.verifications import MAX_PIXELES

    assert MAX_PIXELES < 2 * Image.MAX_IMAGE_PIXELS


RESPUESTA_QUE_SE_CALLA_LA_DISCREPANCIA = {
    "decision": "approve",
    "summary": "El documento parece correcto segun lo comprobado.",
    "groundings": [
        {
            "signal_id": "mrz.checks_ok",
            "cited_value": "True",
            "weight": "in_favor",
            "text": "Los digitos de control de la MRZ cuadran todos.",
        }
    ],
}


def _con_apellido_retocado():
    """Un anverso cuyo apellido no coincide con el de la MRZ.

    Se retoca lo impreso y se deja la MRZ original, que es el fraude que
    solo ve el cotejo entre las dos copias del mismo dato.
    """
    from app.synthetic.tampering import retouch_front

    datos = persona()
    retocados, mrz_original = retouch_front(datos, surnames="WALTEROZ")
    return render_front(retocados), render_back(datos, mrz_lines=mrz_original)


def test_una_explicacion_verdadera_pero_incompleta_queda_marcada(tmp_path, creadas):
    """El caso que justifica la segunda metrica, de punta a punta.

    El agente aprueba citando con toda exactitud que la MRZ cuadra, y se
    calla que el apellido del anverso no coincide con el de la MRZ. La
    respuesta tiene que decir que es fiel Y que esta incompleta, y decir
    tambien QUE se callo: un booleano a secas obliga a buscarlo a mano
    entre 28 senales.
    """
    cliente = api(
        cliente_falso(tmp_path, json.dumps(RESPUESTA_QUE_SE_CALLA_LA_DISCREPANCIA))
    )
    anverso, reverso = _con_apellido_retocado()

    cuerpo = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("anverso.png", png(anverso), "image/png"),
            "reverso": ("reverso.png", png(reverso), "image/png"),
        },
    ).json()
    creadas.append(uuid.UUID(cuerpo["id"]))

    assert cuerpo["explicacion_fiel"] is True
    assert cuerpo["explicacion_completa"] is False
    assert "cross.surnames" in cuerpo["senales_adversas_omitidas"]


def test_la_senal_callada_queda_marcada_en_su_propia_fila(tmp_path, creadas):
    """Lo que permite preguntar que se calla el agente mas a menudo.

    Si las omisiones vivieran como una lista de texto en la cabecera, esa
    consulta seria un LIKE sobre una cadena. Aqui es un WHERE.
    """
    cliente = api(
        cliente_falso(tmp_path, json.dumps(RESPUESTA_QUE_SE_CALLA_LA_DISCREPANCIA))
    )
    anverso, reverso = _con_apellido_retocado()
    cuerpo = cliente.post(
        "/verificaciones",
        files={
            "anverso": ("anverso.png", png(anverso), "image/png"),
            "reverso": ("reverso.png", png(reverso), "image/png"),
        },
    ).json()
    verificacion_id = uuid.UUID(cuerpo["id"])
    creadas.append(verificacion_id)

    with engine.connect() as conexion:
        calladas = conexion.execute(
            sa.select(verificacion_senales.c.signal_id).where(
                verificacion_senales.c.verificacion_id == verificacion_id,
                verificacion_senales.c.omitida.is_(True),
            )
        ).scalars().all()
        adversas = conexion.execute(
            sa.select(verificacion_senales.c.signal_id).where(
                verificacion_senales.c.verificacion_id == verificacion_id,
                verificacion_senales.c.adversa.is_(True),
            )
        ).scalars().all()

    assert list(calladas) == ["cross.surnames"]
    assert list(adversas) == ["cross.surnames"]

    # Y la cabecera tiene que decir lo mismo que sus filas.
    #
    # Sin esta comprobacion la columna no la miraba nadie: mutar el codigo
    # para que guardara siempre `True` dejaba pasar los diecisiete tests,
    # porque todos miraban la respuesta HTTP y ninguno releia lo guardado.
    registro = cliente.get(f"/verificaciones/{verificacion_id}").json()
    assert registro["explicacion_completa"] is False
    assert registro["explicacion_fiel"] is True


def test_un_documento_limpio_sale_completo(tmp_path, creadas):
    """Sin senales adversas no hay nada que callar, y eso no es merito.

    Se comprueba igualmente para que la metrica no de incompleto por
    defecto, que seria el error simetrico y mucho mas ruidoso.
    """
    cliente = api(cliente_falso(tmp_path, json.dumps(RESPUESTA_APROBACION)))

    cuerpo = subir(cliente).json()
    creadas.append(uuid.UUID(cuerpo["id"]))

    assert cuerpo["explicacion_completa"] is True
    assert cuerpo["senales_adversas_omitidas"] == []

    registro = cliente.get(f"/verificaciones/{cuerpo['id']}").json()
    assert registro["explicacion_completa"] is True
    assert all(not senal["adversa"] for senal in registro["senales"])
