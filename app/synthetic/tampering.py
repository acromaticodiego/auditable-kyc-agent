"""Manipulaciones de documento para el conjunto de evaluacion.

Todas parten de una cedula coherente y rompen la coherencia **de una forma
concreta**, porque cada forma deja un rastro distinto y el sistema deberia
poder distinguirlas:

- Retocar el anverso y olvidar la MRZ es el fraude frecuente: quien edita
  una foto de un documento cambia lo que se ve, no lo que parece un
  amasijo de letras del reverso.
- Retocar la MRZ y dejar el anverso es el caso contrario, menos habitual,
  y se detecta igual: las dos copias del dato dejan de coincidir.
- Romper un digito de control es el descuido de quien si edita la MRZ pero
  no sabe que lleva aritmetica dentro.

Hay una cuarta manipulacion que **no deja rastro**: cambiar el dato en el
anverso y recalcular la MRZ entera.  Esta aqui a proposito, porque el
conjunto tiene que incluir lo que el sistema no puede ver.  Un conjunto
donde todos los fraudes son detectables mide el techo y lo llama acierto.
"""

from __future__ import annotations

from app.signals.mrz import build_td1, check_digit
from app.synthetic.cedula import CedulaData


def retouch_front(data: CedulaData, **changes) -> tuple[CedulaData, list[str]]:
    """Cambia lo impreso en el anverso y deja la MRZ original.

    Devuelve los datos que se imprimiran y la MRZ que hay que pintar en el
    reverso, que ya no les corresponde.
    """
    if not changes:
        raise ValueError("retouch_front necesita al menos un campo que cambiar")
    return data.with_changes(**changes), data.mrz()


def retouch_mrz(data: CedulaData, **changes) -> tuple[CedulaData, list[str]]:
    """Cambia la MRZ y deja el anverso original.

    La MRZ resultante es internamente consistente -- sus digitos cuadran --
    pero contradice lo impreso.  Solo el cotejo entre las dos copias del
    dato lo detecta; los digitos de control por si solos no dicen nada.
    """
    if not changes:
        raise ValueError("retouch_mrz necesita al menos un campo que cambiar")
    return data, data.with_changes(**changes).mrz()


def forge_consistently(data: CedulaData, **changes) -> tuple[CedulaData, list[str]]:
    """Cambia el dato y recalcula la MRZ: falsificacion sin rastro aritmetico.

    El documento resultante es coherente consigo mismo y ninguna señal dura
    lo detecta.  Existe para que el conjunto pueda medir ese limite en vez
    de esconderlo.
    """
    if not changes:
        raise ValueError("forge_consistently necesita al menos un campo que cambiar")
    forged = data.with_changes(**changes)
    return forged, forged.mrz()


CHECK_POSITIONS = {
    "numero_documento": (0, 14),
    "fecha_nacimiento": (1, 6),
    "fecha_expiracion": (1, 14),
    "compuesto": (1, 29),
}


def break_check_digit(lines: list[str], which: str = "compuesto") -> list[str]:
    """Estropea un digito de control concreto, dejando los datos intactos.

    Se suma uno al digito impreso en vez de poner una cifra al azar: un
    valor aleatorio podria coincidir con el correcto y producir un caso
    etiquetado como fraude que en realidad cuadra.
    """
    if which not in CHECK_POSITIONS:
        raise ValueError(
            f"digito desconocido: {which!r}; hay {sorted(CHECK_POSITIONS)}"
        )
    row, column = CHECK_POSITIONS[which]
    broken = list(lines)
    current = broken[row][column]
    replacement = str((int(current) + 1) % 10) if current.isdigit() else "1"
    broken[row] = broken[row][:column] + replacement + broken[row][column + 1 :]
    return broken


def expire(data: CedulaData, on: object) -> CedulaData:
    """Un documento caducado, coherente por lo demas.

    No es fraude: es un documento autentico que ya no vale.  Merece una
    decision distinta de la de un documento manipulado, y por eso vive
    aparte de las funciones de retoque.
    """
    return data.with_changes(expiry_date=on)


__all__ = [
    "CHECK_POSITIONS",
    "break_check_digit",
    "build_td1",
    "check_digit",
    "expire",
    "forge_consistently",
    "retouch_front",
    "retouch_mrz",
]
