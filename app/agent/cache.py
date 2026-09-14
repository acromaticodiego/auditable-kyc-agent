"""Cache en disco de las respuestas del modelo.

No es una optimizacion: es lo que hace posible iterar sobre el prompt con
un cupo de 20 peticiones al dia (ver docs/adr/0001).  Repetir una tanda de
evaluacion sin haber tocado el prompt cuesta cero peticiones.

La clave es el hash de la peticion completa, incluido el nombre del modelo:
dos modelos distintos ante el mismo prompt son dos sistemas distintos y no
pueden compartir entrada de cache.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


class ResponseCache:
    def __init__(self, directory: Path | str = ".llm_cache") -> None:
        self.directory = Path(directory)

    @staticmethod
    def key(model: str, request: dict) -> str:
        # sort_keys porque el orden de las claves de un dict no debe cambiar
        # la identidad de la peticion; ensure_ascii=False para que un acento
        # en el prompt no genere dos claves distintas segun como se escriba.
        canonical = json.dumps(
            {"model": model, "request": request},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        except (json.JSONDecodeError, KeyError, OSError):
            # Una entrada corrupta se ignora en vez de romper la tanda: el
            # coste de volver a pedirla es una peticion, el de abortar una
            # evaluacion a medias es mucho mayor.
            return None

    def put(self, key: str, model: str, request: dict, response: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": datetime.now(UTC).isoformat(),
            "model": model,
            "request": request,
            "response": response,
        }
        self._path(key).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
