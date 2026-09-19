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
    senales = _MEDICIONES[cuerpo["ficha"]].senales
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


def test_la_pantalla_ensena_el_documento_que_se_midio(api, documento):
    """El modelo no ve la imagen; quien revisa la decision si deberia verla.

    Sin esto hay que descargar el PNG y abrirlo por fuera, que es justo lo
    que una consola de revision existe para evitar.
    """
    cliente = api()

    cuerpo = _subir(cliente, documento)

    assert set(cuerpo["imagenes"]) == {"anverso", "reverso"}
    respuesta = cliente.get(cuerpo["imagenes"]["anverso"])
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"] == "image/jpeg"


def test_no_se_guarda_el_documento_original_sino_una_copia_reducida(api, documento):
    """Guardar el original convertiria la demo en un almacen de cedulas.

    El resto del sistema evita eso a proposito: `POST /verificaciones` no
    guarda las imagenes, solo su SHA-256. La pantalla no puede ser la
    puerta de atras por la que si se quedan.
    """
    anverso_subido, _ = documento
    cliente = api()

    cuerpo = _subir(cliente, documento)
    servida = cliente.get(cuerpo["imagenes"]["anverso"]).content

    assert servida != anverso_subido
    assert servida[:3] == b"\xff\xd8\xff"  # JPEG, no el PNG que se subio
    assert len(servida) < len(anverso_subido)


def test_la_vista_previa_de_una_medicion_que_ya_no_esta_da_404(api):
    cliente = api()

    respuesta = cliente.get("/demo/mediciones/noexiste/imagen/anverso")

    assert respuesta.status_code == 404


def test_se_puede_fabricar_una_cedula_para_no_grabar_la_real(api):
    """Un video se queda en internet para siempre.

    Sacar ahi un documento autentico regala el NUIP, la fecha de
    nacimiento y la MRZ entera de alguien, y eso no se deshace despues.
    El sistema fabrica uno falso y coherente para poder grabarlo.
    """
    cliente = api()

    respuesta = cliente.post("/demo/cedula")

    assert respuesta.status_code == 200
    d = respuesta.json()
    assert d["con_retrato"] is False
    assert "WALTEROS" in d["identidad"]
    assert set(d["imagenes"]) == {"anverso", "reverso"}


def test_la_cedula_fabricada_se_sirve_en_png_y_no_en_jpeg(api):
    """Va a pasar por el OCR dos veces: la pantalla y luego la camara.

    Meterle perdidas de JPEG antes siquiera de imprimirla seria degradarla
    gratis justo en los bordes de las letras pequenas, que es de donde
    vive el OCR.
    """
    cliente = api()

    d = cliente.post("/demo/cedula").json()
    servida = cliente.get(d["imagenes"]["anverso"])

    assert servida.headers["content-type"] == "image/png"
    assert servida.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_la_cedula_fabricada_la_lee_el_sistema_entera(api):
    """Si no, no serviria para una demostracion: la MRZ tiene que cuadrar."""
    cliente = api()

    fabricada = cliente.post("/demo/cedula").json()
    anverso = cliente.get(fabricada["imagenes"]["anverso"]).content
    reverso = cliente.get(fabricada["imagenes"]["reverso"]).content

    cuerpo = cliente.post(
        "/demo/medir",
        files={
            "anverso": ("a.png", anverso, "image/png"),
            "reverso": ("r.png", reverso, "image/png"),
        },
    ).json()

    senales = {s["id"]: s for s in cuerpo["senales"]}
    assert senales["mrz.checks_ok"]["valor"] is True
    assert senales["cross.nuip"]["valor"] == "match"
    assert cuerpo["linea_base"]["decision"] == "approve"


def test_sin_retrato_no_hay_cotejo_facial_y_se_dice_por_que(api):
    """El marcador gris no es una cara, y la senal no puede fingir que si.

    Una senal facial inventada sobre un documento sin foto seria
    exactamente la clase de medicion que este proyecto no se permite.
    """
    cliente = api()

    fabricada = cliente.post("/demo/cedula").json()
    anverso = cliente.get(fabricada["imagenes"]["anverso"]).content
    reverso = cliente.get(fabricada["imagenes"]["reverso"]).content

    cuerpo = cliente.post(
        "/demo/medir",
        files={
            "anverso": ("a.png", anverso, "image/png"),
            "reverso": ("r.png", reverso, "image/png"),
            "selfie": ("s.png", anverso, "image/png"),
        },
    ).json()

    facial = next(s for s in cuerpo["senales"] if s["id"] == "facial.similarity")
    assert facial["disponible"] is False
    assert facial["motivo_no_disponible"]


def test_una_captura_limpia_no_tiene_nada_que_reprochar(api, documento):
    cliente = api()

    cuerpo = _subir(cliente, documento)

    assert cuerpo["diagnostico"] == []


def test_una_captura_mala_explica_que_le_pasa_en_castellano(api):
    """Sin esto hay que leerse las 28 senales para saber que fallo.

    Paso de verdad: se fotografio el documento en la pantalla de un movil,
    el sistema pidio otra foto -que era lo correcto- y averiguar por que
    exigio repasar senal por senal. Delante de una camara eso no sirve.
    """
    from app.evaluation.catalog import person
    from app.synthetic.cedula import render_back, render_front
    from app.synthetic.degradation import blur, glare

    datos = person()
    anverso = glare(blur(render_front(datos), radius=3.5))
    reverso = blur(render_back(datos), radius=3.5)

    cliente = api()
    cuerpo = cliente.post(
        "/demo/medir",
        files={
            "anverso": ("a.png", _png(anverso), "image/png"),
            "reverso": ("r.png", _png(reverso), "image/png"),
        },
    ).json()

    quejas = " ".join(p["que"] for p in cuerpo["diagnostico"])
    assert "desenfocada" in quejas
    assert "reflejo" in quejas
    assert "MRZ" in quejas
    # Cada queja trae el numero al lado: un diagnostico sin la medicion que
    # lo sostiene es justo la clase de afirmacion que este proyecto audita.
    assert all(p["dato"] for p in cuerpo["diagnostico"])


def test_el_diagnostico_usa_los_mismos_cortes_que_la_linea_base(api):
    """Duplicar los umbrales en la pantalla los dejaria separarse en silencio.

    Si alguien moviera `Thresholds.min_sharpness` para que la linea base
    decidiera distinto, una copia en el codigo de la pantalla seguiria
    explicando la decision con el umbral viejo, y nadie lo notaria porque
    las dos cifras se leen en sitios distintos.
    """
    from app.evaluation.baseline import Thresholds
    from app.evaluation.catalog import person
    from app.synthetic.cedula import render_back, render_front
    from app.synthetic.degradation import blur

    datos = person()
    cliente = api()
    cuerpo = cliente.post(
        "/demo/medir",
        files={
            "anverso": ("a.png", _png(blur(render_front(datos), radius=3.5)), "image/png"),
            "reverso": ("r.png", _png(blur(render_back(datos), radius=3.5)), "image/png"),
        },
    ).json()

    nitidez = next(p for p in cuerpo["diagnostico"] if "desenfocada" in p["que"])
    assert f"hace falta {Thresholds().min_sharpness}" in nitidez["dato"]
