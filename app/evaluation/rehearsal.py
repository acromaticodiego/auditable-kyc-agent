"""Un doble del modelo para ensayar una tanda sin gastar cupo.

No sirve para medir nada y no pretende hacerlo.  Sirve para responder una
pregunta distinta y muy practica: **¿funciona el arnes de evaluacion de
principio a fin?**

El motivo es el orden en que salen las cosas mal.  La tanda de calibracion
cuesta doce peticiones de las veinte del dia, y un fallo en el informe --
una division por cero al contar, una clave que no existe, un formato que
revienta con cierta decision -- solo aparece **despues** de haberlas
gastado.  Ese fallo se arregla en dos minutos y la espera hasta el dia
siguiente dura veinticuatro horas.

El doble responde lo que responderia la linea base de reglas fijas ante esas
mismas senales, serializado como si viniera del modelo.  Se eligio asi y no
con una respuesta fija por dos motivos: las decisiones salen **variadas**
(aprobar, rechazar, pedir reenvio) y por tanto recorren las ramas del
informe, y salen **validas** segun el contrato, que es lo que hace falta
para que el bucle llegue hasta el final.

Que ademas el resultado se parezca al de la linea base es una coincidencia
util para revisar el informe a ojo, y una trampa si alguien lo confunde con
una medida.  Por eso el modelo se llama `ensayo-sin-modelo` y aparece con
ese nombre en la cabecera del informe.
"""

from __future__ import annotations

from app.agent.gemini import ModelResponse
from app.agent.prompt import build_prompt
from app.domain.signals import SignalSet
from app.evaluation.baseline import decide as decide_baseline

MODELO_DE_ENSAYO = "ensayo-sin-modelo"


class _PresupuestoDeEnsayo:
    """Un presupuesto que nunca se gasta, porque aqui no se pide nada."""

    daily_limit = None

    def remaining(self, model: str) -> None:
        return None

    def spent(self, model: str) -> int:
        return 0

    def spent_by_other_keys(self, model: str) -> int:
        return 0

    def ensure_available(self, model: str) -> None:
        return None

    def record(self, model: str) -> None:
        return None


class ModeloDeEnsayo:
    """Imita al cliente de Gemini lo justo para que `run_agent` no note nada.

    Se indexa por el texto del prompt porque es lo que `run_agent` le pasa y
    `build_prompt` es determinista: el mismo juego de senales produce siempre
    el mismo prompt.  Si un prompt llega sin registrar es un fallo del arnes
    y no algo que deba disimularse, asi que revienta en vez de inventarse
    una respuesta.
    """

    def __init__(self, senales_por_caso: dict[str, SignalSet]) -> None:
        self.model = MODELO_DE_ENSAYO
        self.max_retries = 0
        self.budget = _PresupuestoDeEnsayo()
        self._por_prompt = {
            build_prompt(senales): senales for senales in senales_por_caso.values()
        }

    def is_cached(self, prompt: str, response_schema: dict) -> bool:
        """Todo sale "de cache": ninguna vuelta de este doble cuesta nada."""
        return True

    def generate_json(self, prompt: str, response_schema: dict) -> ModelResponse:
        senales = self._por_prompt.get(prompt)
        if senales is None:
            raise KeyError(
                "el ensayo recibio un prompt que no tenia registrado. Suele "
                "significar que las senales se calcularon dos veces con "
                "parametros distintos, por ejemplo con fechas distintas."
            )

        decision = decide_baseline(senales)
        return ModelResponse(
            text=decision.model_dump_json(),
            model=self.model,
            from_cache=True,
            raw={},
        )

    def close(self) -> None:
        return None
