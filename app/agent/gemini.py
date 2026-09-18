"""Cliente de la API de Gemini.

Se habla con la API REST directamente en vez de usar el SDK oficial. El
motivo es el cupo: con un cupo diario pequeno hace falta ver el cuerpo
exacto de un 429 para saber si se agoto el minuto o el dia, y los SDK
tienden a envolver ese detalle en una excepcion generica.  A cambio hay que
construir el payload a mano, que son veinte lineas.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.agent.budget import RequestBudget, account_fingerprint
from app.agent.cache import ResponseCache

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# Codigos que merecen reintento.  El 503 de Gemini ("high demand") aparece
# de verdad y con frecuencia: en una prueba de cinco modelos, tres lo
# devolvieron.  El 429 NO esta aqui a proposito, ver QuotaExhausted.
#
# Ojo con reintentar mucho: un 503 consume cupo igual que una respuesta
# buena.  Doce peticiones fallidas en dos minutos agotaron el cupo diario
# de 20 sin producir una sola decision, asi que el reintento por defecto
# es uno y no dos.
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class GeminiError(RuntimeError):
    pass


# Cuanto se espera como maximo a que vuelva el cupo por minuto.  El cuerpo
# del 429 trae un `retryDelay` propio y se respeta, pero con un tope: si la
# API pidiera esperar diez minutos, una tanda de trece casos se quedaria
# colgada mas de dos horas y es mejor que falle y se repita.
MAX_ESPERA_POR_MINUTO = 75.0


def _quota_por_minuto(body: str) -> tuple[bool, float]:
    """Distingue el 429 del minuto del 429 del dia, y cuanto pide esperar.

    Es la razon por la que este cliente habla REST en vez de usar el SDK, y
    durante un tiempo fue una razon sin cumplir: el codigo trataba los dos
    429 igual y paraba la tanda entera cuando lo unico agotado era el
    minuto, que vuelve solo.

    Google los distingue en `details[].violations[].quotaId`: el diario dice
    `...PerDayPerProjectPerModel`, el del minuto dice `PerMinute`.  Si el
    cuerpo no se deja leer se devuelve "no es por minuto", que es la
    suposicion prudente: esperar por un cupo diario no lo trae de vuelta.
    """
    try:
        datos = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return False, 0.0

    # Un 429 puede traer un cuerpo con la forma que le de la gana, y se ve:
    # basto un `{"error": "quota"}` para que esto reventara con un
    # AttributeError en mitad del manejo de otro error, que es el sitio
    # donde menos falta hace una excepcion nueva. Lo destapo un test.
    if not isinstance(datos, dict) or not isinstance(datos.get("error"), dict):
        return False, 0.0

    detalles = datos["error"].get("details") or []
    if not isinstance(detalles, list):
        return False, 0.0
    por_minuto = False
    espera = 0.0

    for detalle in detalles:
        if not isinstance(detalle, dict):
            continue
        for violacion in detalle.get("violations") or []:
            if not isinstance(violacion, dict):
                continue
            if "perminute" in str(violacion.get("quotaId", "")).lower():
                por_minuto = True
        retraso = detalle.get("retryDelay")
        if isinstance(retraso, str) and retraso.endswith("s"):
            try:
                espera = float(retraso[:-1])
            except ValueError:
                espera = 0.0

    return por_minuto, espera


class QuotaExhausted(GeminiError):
    """El cupo diario se agoto.

    Se distingue del resto de errores porque la reaccion es distinta: no se
    reintenta, se para la tanda.  Insistir sobre un 429 de cupo diario solo
    gasta tiempo, y sobre uno por minuto gasta el del minuto siguiente.
    """


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str
    from_cache: bool
    raw: dict


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        cache: ResponseCache | None = None,
        # 60 segundos no bastaban: el modelo tarda mas cuando se le impone
        # un esquema de respuesta, y el timeout llegaba como un traceback
        # de httpcore en vez de como un error con sentido.
        timeout: float = 180.0,
        max_retries: int = 1,
        # Esperas por el cupo POR MINUTO, con su propio contador.
        #
        # No comparte cuenta con `max_retries` y el motivo es que las dos
        # situaciones cuestan cosas distintas: un 5xx reintentado SI gasta
        # cupo, y por eso una tanda pone max_retries=0.  Un 429 por minuto
        # NO lo gasta, porque la peticion ni se proceso, y ademas vuelve
        # solo en segundos.
        #
        # Colgarlas del mismo contador tuvo una consecuencia concreta: una
        # tanda con max_retries=0 se moria ante un limite por minuto que
        # se arreglaba esperando. Con catorce peticiones seguidas sobre un
        # conjunto que solo se puede medir una vez, eso no es aceptable.
        max_per_minute_waits: int = 3,
        backoff_seconds: float = 2.0,
        budget: RequestBudget | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise GeminiError(
                "falta GEMINI_API_KEY; copiar .env.example a .env y rellenarla"
            )
        self.api_key = api_key
        self.model = model
        self.cache = cache or ResponseCache()
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_per_minute_waits = max_per_minute_waits
        self.backoff_seconds = backoff_seconds
        self.budget = budget or RequestBudget(account=account_fingerprint(api_key))
        # Inyectables para poder probar el manejo de errores de la API
        # (cupo agotado, sobrecarga, corte) sin gastar peticiones reales
        # ni esperar de verdad a que pase el retroceso.
        self._http = http_client or httpx.Client(timeout=timeout)
        self._sleep = sleep

    def _request(self, prompt: str, response_schema: dict) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": response_schema,
                # Temperatura cero: la decision sobre un KYC no deberia
                # cambiar entre dos ejecuciones identicas.  No garantiza
                # determinismo -- la API no lo promete -- pero quita la
                # variacion que si esta bajo nuestro control, y sin ella la
                # cache guardaria una respuesta entre varias posibles.
                "temperature": 0.0,
            },
        }

    def is_cached(self, prompt: str, response_schema: dict) -> bool:
        """Si esta peticion se responderia desde disco, sin gastar cupo.

        Existe para poder contar el coste de una tanda ANTES de empezarla.
        Sin esto, una evaluacion de 12 casos con 3 peticiones de cupo se
        descubre a medias: se gastan las tres, se aborta, y los nueve
        restantes quedan sin medir hasta el dia siguiente.
        """
        key = ResponseCache.key(self.model, self._request(prompt, response_schema))
        return self.cache.get(key) is not None

    def generate_json(self, prompt: str, response_schema: dict) -> ModelResponse:
        request = self._request(prompt, response_schema)

        key = ResponseCache.key(self.model, request)
        cached = self.cache.get(key)
        if cached is not None:
            return ModelResponse(
                text=_extract_text(cached),
                model=self.model,
                from_cache=True,
                raw=cached,
            )

        body = self._post_with_retry(request).json()
        # Solo se cachean las respuestas buenas: guardar un error lo haria
        # permanente hasta borrar el directorio a mano.
        self.cache.put(key, self.model, request, body)
        return ModelResponse(
            text=_extract_text(body), model=self.model, from_cache=False, raw=body
        )

    def _post_with_retry(self, request: dict) -> httpx.Response:
        url = f"{API_ROOT}/{self.model}:generateContent"
        last_error: GeminiError | None = None
        esperas_por_minuto = 0

        # El bucle no puede ir sobre `max_retries` a secas: una espera por
        # cupo del minuto no es un reintento de los que ese numero acota.
        attempt = -1
        while True:
            attempt += 1
            if attempt > self.max_retries + esperas_por_minuto:
                break
            # El intento se anota antes de lanzarlo: un 503 o un corte por
            # tiempo gastan cupo igual que una respuesta buena, asi que
            # contarlos solo al acertar volveria a dejar la cuenta ciega.
            self.budget.ensure_available(self.model)
            self.budget.record(self.model)
            try:
                response = self._http.post(
                    url, params={"key": self.api_key}, json=request
                )
            except httpx.ConnectError as error:
                # No se reintenta: si no hay ruta hasta la API, insistir dos
                # veces mas no la crea.
                raise GeminiError(
                    f"no se pudo conectar con la API de Gemini: {error}. "
                    "Si el fallo menciona el certificado, revisar si hay un "
                    "antivirus interceptando TLS antes de mirar el codigo."
                ) from error
            except httpx.TimeoutException as error:
                last_error = GeminiError(
                    f"la API no respondio en {self.timeout:.0f}s "
                    f"(intento {attempt + 1} de {self.max_retries + 1})"
                )
                last_error.__cause__ = error
            except httpx.HTTPError as error:
                # Cualquier otro fallo de transporte: la conexion se corta a
                # mitad de la respuesta, el servidor habla mal el protocolo,
                # el DNS se cae.
                #
                # Esta rama existe por una tanda que reviento a medias. Solo
                # se capturaban ConnectError y TimeoutException, y un
                # RemoteProtocolError se escapo hasta arriba y mato la
                # ejecucion entera en el caso octavo de trece. Las respuestas
                # anteriores se salvaron por la cache, pero el informe no
                # llego a existir.
                #
                # Es exactamente lo que `run_agent` promete que no pasa: una
                # tanda no puede abortar porque un caso falle. La promesa
                # dependia de que el cliente tradujera TODOS los fallos de
                # httpx a GeminiError, y no lo hacia.
                last_error = GeminiError(
                    f"fallo de transporte contra la API "
                    f"({type(error).__name__}: {error})"
                )
                last_error.__cause__ = error
            else:
                if response.status_code == 429:
                    por_minuto, pedido = _quota_por_minuto(response.text)
                    # El cupo por minuto vuelve solo; el diario no. Esperar
                    # por el primero salva la tanda, esperar por el segundo
                    # solo gasta tiempo.
                    if por_minuto and esperas_por_minuto < self.max_per_minute_waits:
                        esperas_por_minuto += 1
                        self._sleep(min(pedido or 60.0, MAX_ESPERA_POR_MINUTO))
                        last_error = GeminiError(
                            "cupo por minuto agotado; se reintento tras esperar"
                        )
                        continue
                    if por_minuto:
                        raise QuotaExhausted(
                            "cupo POR MINUTO agotado y sin reintentos "
                            f"disponibles. Vuelve solo en unos segundos: "
                            f"{response.text[:300]}"
                        )
                    raise QuotaExhausted(response.text)
                if response.status_code in RETRYABLE_STATUS:
                    last_error = GeminiError(
                        f"HTTP {response.status_code}: {response.text}"
                    )
                elif response.status_code >= 400:
                    raise GeminiError(f"HTTP {response.status_code}: {response.text}")
                else:
                    return response

            if attempt < self.max_retries:
                self._sleep(self.backoff_seconds * (2**attempt))

        assert last_error is not None
        raise last_error

    def close(self) -> None:
        self._http.close()


def _extract_text(body: dict) -> str:
    """Saca el texto de la respuesta, explicando por que no lo hay si falta.

    Una respuesta sin texto casi nunca es un fallo de red: suele ser el
    filtro de seguridad o un corte por longitud, y el motivo viene en
    `finishReason`.  Devolver una cadena vacia aqui convertiria eso en un
    error de parseo de JSON diez lineas mas abajo, que no dice nada.
    """
    candidates = body.get("candidates") or []
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "desconocido")
        raise GeminiError(f"la respuesta no trae candidatos (motivo: {reason})")

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts") or []
    if not parts:
        raise GeminiError(
            f"candidato sin contenido (finishReason: {candidate.get('finishReason')})"
        )

    return "".join(part.get("text", "") for part in parts)


def parse_json_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise GeminiError(
            f"la respuesta no es JSON valido pese al esquema impuesto: {text[:400]}"
        ) from error
