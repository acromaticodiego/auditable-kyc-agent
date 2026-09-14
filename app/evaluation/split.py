"""Particion del conjunto de evaluacion en calibracion y reservado.

La regla del proyecto es que los umbrales y el prompt se ajusten sobre una
mitad y se midan sobre la otra.  Esa regla no se puede dejar en manos de
la disciplina de quien ejecuta la herramienta: basta con mirar el
reservado "solo para ver como va" una vez para que deje de ser reservado,
y no queda rastro de que haya pasado.

Asi que la herramienta lo impone.  Pedir el conjunto reservado sin decir
explicitamente que es la medicion final lanza una excepcion.

El motivo viene de un error que costo caro en otro proyecto: un umbral
elegido sobre una sola sesion de captura daba 0 % de error, y aplicado a
datos que no habian participado en elegirlo dejaba fuera a una de cada
cinco personas reales.  Un numero medido sobre los datos que lo eligieron
no es optimista, es enganoso.
"""

from __future__ import annotations

import hashlib

CALIBRATION = "calibration"
HOLDOUT = "holdout"
SPLITS = (CALIBRATION, HOLDOUT)


class HoldoutLocked(RuntimeError):
    """Se pidio el conjunto reservado sin declarar que es la medicion final."""


def split_of(case_id: str) -> str:
    """A que mitad pertenece un caso.

    Se decide por el hash de su identificador y no por su posicion en la
    lista: asi anadir un caso nuevo no reordena los que ya estaban, y un
    caso cae siempre en la misma mitad por mucho que crezca el catalogo.
    Si la particion dependiera del indice, cada caso nuevo movería a otros
    de lado y las medidas viejas dejarian de ser comparables.
    """
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    return HOLDOUT if digest[0] % 2 else CALIBRATION


def check_access(split: str, *, final_measurement: bool = False) -> None:
    """Deja pasar la calibracion siempre y el reservado solo si se declara.

    `final_measurement=True` no es una contrasena: es una declaracion.
    Quien la escribe esta diciendo que este numero es el que se publica y
    que a partir de aqui el reservado queda quemado para elegir nada.
    """
    if split not in SPLITS:
        raise ValueError(f"particion desconocida: {split!r}; hay {list(SPLITS)}")

    if split == HOLDOUT and not final_measurement:
        raise HoldoutLocked(
            "el conjunto reservado no se mira para ajustar nada. Si de verdad "
            "es la medicion final, pasar final_measurement=True y publicar el "
            "numero tal cual salga; si lo que se quiere es iterar, usar "
            f"{CALIBRATION!r}."
        )
