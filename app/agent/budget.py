"""Presupuesto diario de peticiones a la API del modelo.

Existe por una leccion cara y medida: un 503 de sobrecarga **consume cupo
igual que una respuesta buena**.  Con `max_retries=2`, tres ejecuciones
seguidas de una sonda de tres casos hacen hasta 27 peticiones, y el cupo
diario de este plan es de 20.  Se agoto en poco mas de dos minutos sin
haber obtenido una sola decision util.

Por eso el conteo:

- se lleva por modelo y por clave.  Cuidado con el matiz: el cuerpo del
  429 identifica el cupo como
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, es decir **por
  proyecto**, no por clave.  Dos claves del mismo proyecto de Google
  comparten cupo, y este contador les dara cuentas separadas: en ese caso
  es OPTIMISTA y el 429 llegara antes de lo que diga.  Se reparte por
  clave igualmente porque es lo unico observable desde aqui -- el proyecto
  no viaja en la credencial -- y porque distinguir proyectos distintos
  (que si tienen cupos distintos) vale mas que el error en el otro
  sentido;
- se persiste en disco, porque el cupo es diario y el proceso no vive
  tanto;
- el dia se corta en la **medianoche del Pacifico**, no en la de UTC. Lo
  descubrio un 429 a las 00:00:29 UTC: el contador local acababa de
  estrenar dia y conceder 20 peticiones nuevas mientras Google seguia
  contando las del dia anterior, que en el Pacifico eran las cinco de la
  tarde. Cortar el dia siete horas antes que el proveedor abre una ventana
  diaria en la que el contador da permiso para gastar cupo que no existe;
- cuenta el INTENTO, no el exito, que es justo lo que descubrio el
  incidente.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Donde Google corta el dia del cupo gratuito.  No es una preferencia
# regional: es la frontera del proveedor, y el contador local tiene que
# usar la suya y no la de la maquina que ejecuta esto.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")


def account_fingerprint(api_key: str) -> str:
    """Identifica la clave sin guardarla.

    El conteo se reparte por cuenta, asi que hay que distinguir una clave
    de otra en un fichero que vive en disco.  Se guarda un hash corto: basta
    para separar cuentas y no deja la credencial escrita en ningun sitio.

    No identifica el proyecto, que es la unidad real del cupo; ver la nota
    de la cabecera del modulo."
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


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
        account: str = "sin-clave",
    ) -> None:
        self.path = Path(path)
        self.daily_limit = daily_limit
        self._today = today
        self.account = account

    def _slot(self, model: str) -> str:
        return f"{self.account}:{model}"

    def _legacy_slot(self, model: str) -> str:
        """Como se anotaba antes de repartir el conteo por clave.

        Existe por un fallo real y del mismo dia: al pasar de anotar por
        modelo a anotar por clave y modelo, las peticiones ya gastadas
        quedaron bajo la clave vieja y el contador nuevo leyo cero.  Habia
        20 peticiones gastadas y `remaining` decia 20, que es justo la
        ceguera que este modulo existe para evitar.

        Las entradas viejas se suman a las de cualquier clave que pregunte.
        Si dos claves distintas heredan el mismo saldo viejo, el contador
        sera PESIMISTA y avisara antes de tiempo.  Ese es el sentido
        correcto del error: pasarse de prudente cuesta una ejecucion
        pospuesta, quedarse corto cuesta el cupo del dia entero.
        """
        return model

    @property
    def today(self) -> str:
        return (self._today or datetime.now(QUOTA_TIMEZONE).date()).isoformat()

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
        day = self._load().get(self.today, {})
        return day.get(self._slot(model), 0) + day.get(self._legacy_slot(model), 0)

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
        slot = self._slot(model)
        day[slot] = day.get(slot, 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
