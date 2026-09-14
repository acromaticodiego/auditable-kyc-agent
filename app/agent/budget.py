"""Presupuesto diario de peticiones a la API del modelo.

Existe por una leccion cara y medida: un 503 de sobrecarga **consume cupo
igual que una respuesta buena**.  Con `max_retries=2`, tres ejecuciones
seguidas de una sonda de tres casos hacen hasta 27 peticiones, y el cupo
diario de este plan es de 20.  Se agoto en poco mas de dos minutos sin
haber obtenido una sola decision util.

Por eso el conteo:

- se lleva por modelo, porque el cupo es por modelo;
- se persiste en disco, porque el cupo es diario y el proceso no vive
  tanto;
- cuenta el INTENTO, no el exito, que es justo lo que descubrio el
  incidente.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path


class BudgetExhausted(RuntimeError):
    """Se alcanzo el tope local antes de llamar a la API.

    Es distinto de un 429: aqui ni siquiera se llega a gastar la peticion.
    Sirve para que una tanda larga se pare sola en vez de descubrirlo a
    base de errores.
    """


class RequestBudget:
    def __init__(
        self,
        path: Path | str = ".llm_cache/budget.json",
        daily_limit: int | None = 20,
        today: date | None = None,
    ) -> None:
        self.path = Path(path)
        self.daily_limit = daily_limit
        self._today = today

    @property
    def today(self) -> str:
        return (self._today or datetime.now(UTC).date()).isoformat()

    def _load(self) -> dict[str, dict[str, int]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Un fichero corrupto no puede impedir trabajar; se pierde el
            # conteo del dia, que es un mal menor frente a no poder llamar.
            return {}
        return data if isinstance(data, dict) else {}

    def spent(self, model: str) -> int:
        return self._load().get(self.today, {}).get(model, 0)

    def remaining(self, model: str) -> int | None:
        if self.daily_limit is None:
            return None
        return max(0, self.daily_limit - self.spent(model))

    def ensure_available(self, model: str) -> None:
        remaining = self.remaining(model)
        if remaining is not None and remaining <= 0:
            raise BudgetExhausted(
                f"gastadas {self.spent(model)} de {self.daily_limit} peticiones "
                f"de hoy para {model}. Los 503 de sobrecarga tambien cuentan."
            )

    def record(self, model: str) -> None:
        """Anota un intento.  Se llama ANTES de la peticion, no despues.

        Si se anotara solo al recibir un 200, los 503 y los cortes por
        tiempo saldrian gratis en la cuenta local y volveria a pasar lo de
        agotar el cupo sin enterarse.
        """
        data = self._load()
        day = data.setdefault(self.today, {})
        day[model] = day.get(model, 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
