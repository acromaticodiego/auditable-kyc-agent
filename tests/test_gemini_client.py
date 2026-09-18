"""Pruebas del cliente de Gemini con transporte simulado.

Todo lo que hay aqui se prueba sin tocar la API real: el cupo diario es de
20 peticiones y gastarlo en probar el manejo de errores seria absurdo.  Lo
que NO se prueba aqui es si el modelo de verdad respeta el esquema; eso
solo lo contesta una llamada real (scripts/probe_gemini.py).
"""

import datetime
import json

import httpx
import pytest

from app.agent.budget import (
    BudgetExhausted,
    RequestBudget,
    account_fingerprint,
)
from app.agent.cache import ResponseCache
from app.agent.gemini import (
    MAX_ESPERA_POR_MINUTO,
    GeminiClient,
    GeminiError,
    QuotaExhausted,
    parse_json_response,
)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def ok_body(text: str = '{"a": "b"}') -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def build_client(tmp_path, handler, model: str = "gemini-test", **kwargs) -> GeminiClient:
    # El presupuesto va sin tope y en tmp_path salvo que el test diga otra
    # cosa: si apuntara al fichero real, la suite gastaria la cuenta del dia
    # y empezaria a fallar sola al llegar a veinte.
    kwargs.setdefault("budget", RequestBudget(tmp_path / "budget.json", daily_limit=None))
    return GeminiClient(
        api_key="clave-de-prueba",
        model=model,
        cache=ResponseCache(tmp_path),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        # El retroceso no se duerme de verdad: si no, la suite tardaria
        # segundos en probar algo que no depende del reloj.
        sleep=lambda seconds: waited.append(seconds),
        **kwargs,
    )


waited: list[float] = []


def test_una_peticion_repetida_sale_de_cache_sin_tocar_la_red(tmp_path):
    """Es lo que hace viable iterar sobre el prompt con 20 peticiones al dia."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=ok_body())

    client = build_client(tmp_path, handler)

    first = client.generate_json("hola", SCHEMA)
    second = client.generate_json("hola", SCHEMA)

    assert (first.from_cache, second.from_cache) == (False, True)
    assert first.text == second.text
    assert len(calls) == 1


def test_un_prompt_distinto_no_reutiliza_la_entrada_de_cache(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=ok_body())

    client = build_client(tmp_path, handler)

    client.generate_json("hola", SCHEMA)
    response = client.generate_json("adios", SCHEMA)

    assert response.from_cache is False
    assert len(calls) == 2


def test_cambiar_de_modelo_invalida_la_cache(tmp_path):
    """Dos modelos ante el mismo prompt son dos sistemas distintos.

    Si compartieran entrada de cache, rotar el nombre del modelo para
    conseguir cupo nuevo devolveria la respuesta del modelo anterior y la
    tanda mediria algo que nadie ejecuto.
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=ok_body())

    build_client(tmp_path, handler, model="gemini-uno").generate_json("hola", SCHEMA)
    response = build_client(tmp_path, handler, model="gemini-dos").generate_json(
        "hola", SCHEMA
    )

    assert response.from_cache is False
    assert len(calls) == 2


def test_el_cupo_agotado_se_distingue_del_resto_de_errores(tmp_path):
    """Ante un 429 se para la tanda; ante otros errores se puede reaccionar
    distinto.  Por eso es una excepcion propia y no un GeminiError a secas."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="RESOURCE_EXHAUSTED: quota per day")

    client = build_client(tmp_path, handler)

    with pytest.raises(QuotaExhausted, match="quota per day"):
        client.generate_json("hola", SCHEMA)


def test_un_error_http_cualquiera_conserva_el_cuerpo(tmp_path):
    """El cuerpo es donde Google explica que pasa; perderlo obliga a
    adivinar.  Fue literalmente lo que identifico una clave equivocada."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="API key not valid")

    client = build_client(tmp_path, handler)

    with pytest.raises(GeminiError, match="API key not valid"):
        client.generate_json("hola", SCHEMA)


def test_una_respuesta_bloqueada_dice_por_que(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        )

    client = build_client(tmp_path, handler)

    with pytest.raises(GeminiError, match="SAFETY"):
        client.generate_json("hola", SCHEMA)


def test_un_candidato_cortado_dice_el_motivo_del_corte(tmp_path):
    """Sin esto, un corte por longitud llegaria como 'JSON invalido' y se
    perseguiria el error en el sitio equivocado."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}
        )

    client = build_client(tmp_path, handler)

    with pytest.raises(GeminiError, match="MAX_TOKENS"):
        client.generate_json("hola", SCHEMA)


def test_una_respuesta_fallida_no_se_guarda_en_cache(tmp_path):
    """Cachear un error lo volveria permanente hasta borrar el directorio."""
    status = {"code": 500}

    def handler(request: httpx.Request) -> httpx.Response:
        if status["code"] == 500:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=ok_body())

    client = build_client(tmp_path, handler)

    with pytest.raises(GeminiError):
        client.generate_json("hola", SCHEMA)

    status["code"] = 200
    response = client.generate_json("hola", SCHEMA)

    assert response.from_cache is False
    assert response.text == '{"a": "b"}'


def test_sin_clave_el_cliente_no_llega_a_construirse():
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        GeminiClient(api_key="", model="gemini-test")


def test_una_respuesta_que_no_es_json_se_reporta_como_tal():
    with pytest.raises(GeminiError, match="no es JSON valido"):
        parse_json_response("lo siento, no puedo ayudarte con eso")


# --- Sobrecarga de la API y reintentos -------------------------------------


def test_un_503_se_reintenta_y_la_siguiente_respuesta_vale(tmp_path):
    """El 503 de Gemini ("high demand") aparece de verdad: en una prueba de
    cinco modelos, tres lo devolvieron.

    Con el reintento por defecto (uno) hay dos intentos en total, no tres:
    cada reintento gasta cupo y el tope diario es de veinte.
    """
    codes = [503, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        code = codes.pop(0)
        if code == 200:
            return httpx.Response(200, json=ok_body())
        return httpx.Response(code, text="high demand")

    response = build_client(tmp_path, handler).generate_json("hola", SCHEMA)

    assert response.text == '{"a": "b"}'
    assert codes == []


def test_un_503_persistente_acaba_fallando_con_el_cuerpo(tmp_path):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="high demand")

    with pytest.raises(GeminiError, match="high demand"):
        build_client(tmp_path, handler, max_retries=2).generate_json("hola", SCHEMA)

    assert len(calls) == 3  # el intento inicial mas dos reintentos


def test_el_cupo_agotado_no_se_reintenta(tmp_path):
    """Insistir sobre un 429 diario solo gasta tiempo, y sobre uno por
    minuto gasta el cupo del minuto siguiente."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, text="quota per day")

    with pytest.raises(QuotaExhausted):
        build_client(tmp_path, handler, max_retries=2).generate_json("hola", SCHEMA)

    assert len(calls) == 1


def test_un_400_no_se_reintenta(tmp_path):
    """Una clave invalida o un esquema mal formado no mejoran por insistir."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="API key not valid")

    with pytest.raises(GeminiError, match="API key not valid"):
        build_client(tmp_path, handler, max_retries=2).generate_json("hola", SCHEMA)

    assert len(calls) == 1


def test_un_corte_por_tiempo_se_reintenta_y_se_explica(tmp_path):
    """Sin esto el corte llegaba como un traceback de httpcore de cuarenta
    lineas que no decia cuanto se habia esperado."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(GeminiError, match="no respondio en 180s"):
        build_client(tmp_path, handler).generate_json("hola", SCHEMA)


def test_no_se_puede_conectar_y_se_menciona_el_tls(tmp_path):
    """La pista del antivirus interceptando TLS ahorra horas de buscar en
    el sitio equivocado."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate verify failed")

    with pytest.raises(GeminiError, match="antivirus"):
        build_client(tmp_path, handler).generate_json("hola", SCHEMA)


# --- Presupuesto de peticiones ---------------------------------------------


def test_una_respuesta_de_cache_no_gasta_presupuesto(tmp_path):
    """Es la razon de ser de la cache: repetir una tanda sin tocar el prompt
    no debe costar cupo."""
    budget = RequestBudget(tmp_path / "budget.json", daily_limit=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok_body())

    client = build_client(tmp_path, handler, budget=budget)
    client.generate_json("hola", SCHEMA)
    client.generate_json("hola", SCHEMA)

    assert budget.spent("gemini-test") == 1


def test_cada_reintento_por_sobrecarga_gasta_presupuesto(tmp_path):
    """La leccion que costo el cupo de un dia entero: un 503 se paga igual
    que una respuesta buena."""
    budget = RequestBudget(tmp_path / "budget.json", daily_limit=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="high demand")

    with pytest.raises(GeminiError):
        build_client(tmp_path, handler, budget=budget, max_retries=2).generate_json(
            "hola", SCHEMA
        )

    assert budget.spent("gemini-test") == 3


def test_un_corte_por_tiempo_tambien_gasta_presupuesto(tmp_path):
    """No se sabe si la peticion llego; contarla es lo conservador."""
    budget = RequestBudget(tmp_path / "budget.json", daily_limit=None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(GeminiError):
        build_client(tmp_path, handler, budget=budget, max_retries=0).generate_json(
            "hola", SCHEMA
        )

    assert budget.spent("gemini-test") == 1


def test_al_agotarse_el_presupuesto_no_se_llega_a_llamar(tmp_path):
    """Parar en seco es mejor que descubrirlo a base de 429."""
    budget = RequestBudget(tmp_path / "budget.json", daily_limit=2)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=ok_body())

    client = build_client(tmp_path, handler, budget=budget)
    client.generate_json("uno", SCHEMA)
    client.generate_json("dos", SCHEMA)

    with pytest.raises(BudgetExhausted, match="503"):
        client.generate_json("tres", SCHEMA)

    assert len(calls) == 2


def test_el_conteo_sobrevive_al_proceso(tmp_path):
    """El cupo es diario y el proceso no dura un dia; sin persistencia la
    cuenta empezaria de cero en cada ejecucion."""
    path = tmp_path / "budget.json"
    RequestBudget(path).record("gemini-test")

    assert RequestBudget(path).spent("gemini-test") == 1


def test_el_conteo_de_ayer_no_gasta_el_cupo_de_hoy(tmp_path):
    path = tmp_path / "budget.json"
    ayer = RequestBudget(path, today=datetime.date(2026, 9, 13))
    ayer.record("gemini-test")
    ayer.record("gemini-test")

    hoy = RequestBudget(path, today=datetime.date(2026, 9, 14))

    assert (hoy.spent("gemini-test"), hoy.remaining("gemini-test")) == (0, 20)


def test_el_cupo_se_lleva_por_modelo(tmp_path):
    """Rotar el nombre del modelo da cupo nuevo, asi que la cuenta tiene que
    separarlos."""
    path = tmp_path / "budget.json"
    budget = RequestBudget(path)
    budget.record("gemini-uno")

    assert (budget.spent("gemini-uno"), budget.spent("gemini-dos")) == (1, 0)


def test_dos_claves_distintas_no_comparten_cuenta(tmp_path):
    """El cupo es por modelo dentro de cada proyecto de Google, asi que
    cambiar de clave concede una cuenta nueva.  Si el contador no lo
    reflejara, habria que borrarlo a mano cada vez y dejaria de servir."""
    path = tmp_path / "budget.json"
    vieja = RequestBudget(path, account="aaaaaaaaaaaa")
    for _ in range(20):
        vieja.record("gemini-test")

    nueva = RequestBudget(path, account="bbbbbbbbbbbb")

    assert vieja.remaining("gemini-test") == 0
    assert nueva.remaining("gemini-test") == 20
    nueva.ensure_available("gemini-test")


def test_la_huella_de_la_clave_no_contiene_la_clave(tmp_path):
    """El conteo vive en disco; la credencial no puede acabar ahi."""
    clave = "AQ.clave-secreta-que-no-debe-aparecer"
    huella = account_fingerprint(clave)

    RequestBudget(tmp_path / "budget.json", account=huella).record("gemini-test")
    contenido = (tmp_path / "budget.json").read_text(encoding="utf-8")

    assert clave not in contenido
    assert "clave-secreta" not in contenido
    assert huella in contenido


def test_cuenta_las_peticiones_anotadas_con_el_formato_viejo(tmp_path):
    """El fichero de conteo sobrevive al cambio de formato de la clave.

    Paso de verdad y el mismo dia: al empezar a repartir el conteo por
    clave, las 20 peticiones que ya se habian gastado quedaron anotadas
    bajo el nombre del modelo a secas, y el contador nuevo las leyo como
    cero.  Decia que quedaban 20 cuando no quedaba ninguna, que es
    exactamente la ceguera que este contador existe para evitar.
    """
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps({"2026-09-14": {"gemini-test": 20}}), encoding="utf-8"
    )
    budget = RequestBudget(
        path, today=datetime.date(2026, 9, 14), account="aaaaaaaaaaaa"
    )

    assert budget.spent("gemini-test") == 20
    assert budget.remaining("gemini-test") == 0
    with pytest.raises(BudgetExhausted):
        budget.ensure_available("gemini-test")


def test_el_saldo_viejo_se_suma_al_de_la_clave_actual(tmp_path):
    """Las anotaciones de los dos formatos cuentan juntas, no una u otra.

    Si el formato viejo solo se leyera cuando el nuevo esta a cero, la
    primera peticion del dia borraria de la vista todo el saldo anterior.
    """
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps({"2026-09-14": {"gemini-test": 3}}), encoding="utf-8"
    )
    budget = RequestBudget(
        path, today=datetime.date(2026, 9, 14), account="aaaaaaaaaaaa"
    )
    budget.record("gemini-test")

    assert budget.spent("gemini-test") == 4


def test_el_dia_del_cupo_se_corta_en_el_pacifico_y_no_en_utc(monkeypatch):
    """El contador no puede estrenar dia siete horas antes que el proveedor.

    Paso de verdad: a las 00:00:29 UTC el contador local dio por empezado un
    dia nuevo y concedio 20 peticiones, y Google respondio 429 porque en el
    Pacifico eran las cinco de la tarde del dia anterior. Cortar el dia antes
    que el proveedor abre una ventana diaria en la que el contador autoriza a
    gastar cupo que no existe, que es justo lo que este modulo evita.
    """
    class RelojDeMedianocheUTC:
        @staticmethod
        def now(tz):
            instante = datetime.datetime(
                2026, 9, 15, 0, 0, 29, tzinfo=datetime.timezone.utc
            )
            return instante.astimezone(tz)

    monkeypatch.setattr("app.agent.budget.datetime", RelojDeMedianocheUTC)

    assert RequestBudget().today == "2026-09-14"


def test_el_dia_del_cupo_si_cambia_en_la_medianoche_del_pacifico(monkeypatch):
    """La otra mitad del corte: a las 07:00 UTC si empieza el dia nuevo.

    Sin este, el arreglo podria ser 'restar siempre un dia' y nadie lo notaria.
    """
    class RelojDeMedianochePacifico:
        @staticmethod
        def now(tz):
            instante = datetime.datetime(
                2026, 9, 15, 7, 0, 1, tzinfo=datetime.timezone.utc
            )
            return instante.astimezone(tz)

    monkeypatch.setattr("app.agent.budget.datetime", RelojDeMedianochePacifico)

    assert RequestBudget().today == "2026-09-15"


def test_ve_lo_gastado_hoy_por_otras_claves(tmp_path):
    """Rotar la clave deja el contador a cero y Google sigue contando igual.

    El cupo gratuito va por proyecto de Google, no por clave, asi que una
    clave nueva dentro del mismo proyecto no concede nada. Este contador
    reparte por clave porque el proyecto no viaja en la credencial, y esa
    diferencia es la que se cobra: sin este dato, la herramienta diria
    'quedan 20' justo despues de una rotacion y la primera peticion se
    comeria un 429.
    """
    path = tmp_path / "budget.json"
    vieja = RequestBudget(path, account="aaaaaaaaaaaa")
    for _ in range(14):
        vieja.record("gemini-test")

    nueva = RequestBudget(path, account="bbbbbbbbbbbb")

    assert nueva.spent("gemini-test") == 0
    assert nueva.remaining("gemini-test") == 20
    # Pero se puede avisar de que hay 14 gastadas por otra clave.
    assert nueva.spent_by_other_keys("gemini-test") == 14


def test_lo_gastado_por_otras_claves_no_cuenta_otros_modelos(tmp_path):
    """El cupo es por modelo dentro del proyecto, asi que mezclarlos mentiria."""
    path = tmp_path / "budget.json"
    vieja = RequestBudget(path, account="aaaaaaaaaaaa")
    vieja.record("gemini-uno")
    vieja.record("gemini-uno")
    vieja.record("gemini-dos")

    nueva = RequestBudget(path, account="bbbbbbbbbbbb")

    assert nueva.spent_by_other_keys("gemini-uno") == 2
    assert nueva.spent_by_other_keys("gemini-dos") == 1


def test_la_propia_clave_no_se_cuenta_como_ajena(tmp_path):
    """Si se contara a si misma, el aviso saltaria siempre y dejaria de leerse."""
    path = tmp_path / "budget.json"
    mia = RequestBudget(path, account="aaaaaaaaaaaa")
    for _ in range(5):
        mia.record("gemini-test")

    assert mia.spent("gemini-test") == 5
    assert mia.spent_by_other_keys("gemini-test") == 0


@pytest.mark.parametrize(
    "fallo, fragmento",
    [
        (httpx.RemoteProtocolError("corto la respuesta"), "RemoteProtocolError"),
        (httpx.ReadError("se rompio la lectura"), "ReadError"),
        (httpx.WriteError("se rompio la escritura"), "WriteError"),
        # PoolTimeout hereda de TimeoutException, asi que lo recoge la rama
        # del corte por tiempo y su mensaje es el de un timeout. Es correcto
        # y se deja en el barrido para que quede fijado: lo que importa no
        # es que rama lo atienda sino que ninguna lo deje escapar.
        (httpx.PoolTimeout("no habia conexiones libres"), "no respondio"),
    ],
)
def test_ningun_fallo_de_transporte_se_escapa_como_excepcion_de_httpx(
    tmp_path, fallo, fragmento
):
    """Una tanda no puede morir porque un caso falle, y casi muere.

    Solo se capturaban ConnectError y TimeoutException. Un
    RemoteProtocolError se escapo hasta arriba y mato una tanda de trece
    casos en el octavo: las respuestas anteriores se salvaron por la cache,
    pero el informe no llego a existir y el cupo ya estaba gastado.

    La promesa de `run_agent` de devolver los fallos como resultado en vez
    de lanzarlos dependia de que el cliente tradujera TODOS los fallos de
    httpx, asi que aqui se barren varias familias en vez de la que dio
    guerra.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise fallo

    with pytest.raises(GeminiError) as capturado:
        build_client(tmp_path, handler, max_retries=0).generate_json("hola", SCHEMA)

    # Sale como error propio y no como excepcion de la libreria de red.
    assert not isinstance(capturado.value, httpx.HTTPError)
    assert fragmento in str(capturado.value)


def cuerpo_429(quota_id: str, retraso: str = "30s") -> str:
    """Un 429 con la forma real que devuelve Gemini."""
    return json.dumps(
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": quota_id}],
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retraso,
                    },
                ],
            }
        }
    )


DIARIO = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
POR_MINUTO = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def test_el_429_del_minuto_se_reintenta_tras_esperar(tmp_path):
    """El cupo por minuto vuelve solo; parar la tanda por el es tirarla.

    Es la razon por la que este cliente habla REST en vez de usar el SDK, y
    durante un tiempo fue una razon sin cumplir: el codigo trataba los dos
    429 igual.
    """
    esperas: list[float] = []
    respuestas = [
        httpx.Response(429, text=cuerpo_429(POR_MINUTO, "12s")),
        httpx.Response(200, json=ok_body()),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return respuestas.pop(0)

    cliente = build_client(tmp_path, handler, max_retries=1)
    cliente._sleep = esperas.append

    respuesta = cliente.generate_json("un prompt", SCHEMA)

    assert respuesta.text == '{"a": "b"}'
    assert esperas == [12.0], "deberia haber esperado lo que pidio la API"


def test_el_429_del_dia_no_se_reintenta(tmp_path):
    """Esperar por el cupo diario no lo trae de vuelta, solo gasta tiempo."""
    intentos = []

    def handler(request: httpx.Request) -> httpx.Response:
        intentos.append(request)
        return httpx.Response(429, text=cuerpo_429(DIARIO))

    cliente = build_client(tmp_path, handler, max_retries=1)

    with pytest.raises(QuotaExhausted):
        cliente.generate_json("un prompt", SCHEMA)

    assert len(intentos) == 1, "el cupo diario no debe reintentarse"


def test_un_429_ilegible_se_trata_como_diario(tmp_path):
    """La suposicion prudente: esperar por un cupo diario no lo devuelve.

    Si el cuerpo no se deja leer y se supusiera 'por minuto', una tanda se
    quedaria esperando en bucle contra un cupo que no vuelve hasta manana.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="esto no es json")

    with pytest.raises(QuotaExhausted):
        build_client(tmp_path, handler, max_retries=1).generate_json("x", SCHEMA)


def test_la_espera_por_minuto_tiene_tope(tmp_path):
    """Si la API pidiera diez minutos, trece casos serian mas de dos horas."""
    esperas: list[float] = []
    respuestas = [
        httpx.Response(429, text=cuerpo_429(POR_MINUTO, "600s")),
        httpx.Response(200, json=ok_body()),
    ]
    cliente = build_client(tmp_path, lambda r: respuestas.pop(0), max_retries=1)
    cliente._sleep = esperas.append

    cliente.generate_json("un prompt", SCHEMA)

    assert esperas == [MAX_ESPERA_POR_MINUTO]


@pytest.mark.parametrize(
    "cuerpo",
    [
        '{"error": "quota"}',
        '{"error": {"details": "no es una lista"}}',
        '{"error": {"details": ["no es un objeto"]}}',
        '{"error": {"details": [{"violations": "tampoco"}]}}',
        "[]",
        "null",
    ],
)
def test_un_429_con_el_cuerpo_raro_no_revienta(tmp_path, cuerpo):
    """Un cuerpo inesperado no puede lanzar una excepcion nueva.

    Basto un `{"error": "quota"}` para que el parseo reventara con un
    AttributeError, y ocurria dentro del manejo de OTRO error: el sitio
    donde menos falta hace. Se trata como diario, que es lo prudente.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text=cuerpo)

    with pytest.raises(QuotaExhausted):
        build_client(tmp_path, handler, max_retries=1).generate_json("x", SCHEMA)
