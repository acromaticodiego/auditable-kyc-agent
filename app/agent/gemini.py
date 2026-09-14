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

from app.agent.cache import ResponseCache

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# Codigos que merecen reintento.  El 503 de Gemini ("high demand") aparece
# de verdad y con frecuencia: en una prueba de cinco modelos, tres lo
# devolvieron.  El 429 NO esta aqui a proposito, ver QuotaExhausted.
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class GeminiError(RuntimeError):
    pass


class QuotaExhausted(GeminiError):
    """El cupo diario o por minuto se agoto.

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
        max_retries: int = 2,
        backoff_seconds: float = 2.0,
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
        self.backoff_seconds = backoff_seconds
        # Inyectables para poder probar el manejo de errores de la API
        # (cupo agotado, sobrecarga, corte) sin gastar peticiones reales
        # ni esperar de verdad a que pase el retroceso.
        self._http = http_client or httpx.Client(timeout=timeout)
        self._sleep = sleep

    def generate_json(self, prompt: str, response_schema: dict) -> ModelResponse:
        request = {
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

        for attempt in range(self.max_retries + 1):
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
            else:
                if response.status_code == 429:
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
