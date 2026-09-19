"""Dice cual de las claves del .env sigue valiendo hoy.  No gasta cupo.

POR QUE EXISTE
--------------

Una manana se perdio en diagnosticar una caida de Gemini que no existia.
La sonda devolvia "el modelo no contesto" y se fue a mirar si los modelos
estaban caidos, que es lo que habia pasado el 18 de septiembre.  El 401
traia el motivo exacto y nadie lo leyo: la clave habia dejado de valer.

Lo que hace que esto merezca una herramienta y no un `curl` a mano es que
este proyecto trabaja con varias claves de cuentas de Gmail distintas,
porque el cupo gratuito va por proyecto de Google y no por clave.  Saber
cual de ellas responde no deberia costar una peticion de las veinte del
dia, y no la cuesta: `ListModels` no consume cupo de generacion.  Es la
unica pregunta sobre credenciales que se puede hacer gratis.

Distingue tres respuestas que desde fuera se parecen:

  - HTTP 200                 la clave vale.
  - UNAUTHENTICATED          Google no la acepta.  Ni es cupo ni es una
                             caida: manana fallara igual.
  - 5xx                      es Google el que esta mal, no la clave.

COMO SE USA
-----------

Las claves entran por la entrada estandar y no por un fichero ni por un
argumento, a proposito.  El .env no esta montado en el contenedor -solo
lo estan app, tests, scripts y la cache- y pasar una clave como argumento
la dejaria escrita en la linea de ordenes, visible en `ps` y en el
historial del interprete.

    docker compose exec -T api python scripts/check_keys.py < .env

Acepta el formato del propio .env: se queda con las lineas GEMINI_API_KEY,
comentadas o no, y usa como nombre el ultimo comentario suelto anterior,
que es como estan etiquetadas las cuentas en ese fichero.

Nunca imprime una clave.  Para identificarlas usa la misma huella corta
que el diario del cupo, asi que lo que sale aqui se puede cruzar
directamente con .llm_cache/budget.json.
"""

from __future__ import annotations

import re
import sys

import httpx

from app.agent.budget import account_fingerprint

# Listar modelos no consume cupo de generacion, que es justo lo que
# convierte esta comprobacion en gratuita.  Si algun dia hubiera que
# cambiar esta URL por una que si cobre, la herramienta pierde su motivo
# de existir y es mejor borrarla que dejarla mintiendo.
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

LINEA_DE_CLAVE = re.compile(r"^#?\s*GEMINI_API_KEY\s*=\s*(\S+)\s*$")


def leer_claves(texto: str) -> list[tuple[str, str, bool]]:
    """Saca (etiqueta, clave, si esta activa) de un .env, en orden."""
    claves: list[tuple[str, str, bool]] = []
    etiqueta = "sin etiqueta"
    for linea in texto.splitlines():
        limpia = linea.strip()
        # Un comentario suelto -sin '='- es como se marca de quien es la
        # clave que viene debajo.  Los comentarios con '=' son claves
        # desactivadas y no deben pisar la etiqueta.
        if limpia.startswith("#") and "=" not in limpia and len(limpia) > 1:
            etiqueta = limpia.lstrip("#").strip()
            continue
        encontrada = LINEA_DE_CLAVE.match(limpia)
        if encontrada:
            claves.append((etiqueta, encontrada.group(1), not limpia.startswith("#")))
    return claves


def comprobar(clave: str) -> tuple[str, str]:
    """Devuelve (veredicto, detalle) sin gastar cupo."""
    try:
        respuesta = httpx.get(MODELS_URL, params={"key": clave}, timeout=30)
    except httpx.HTTPError as error:
        return "SIN RED", f"{type(error).__name__}: {error}"

    if respuesta.status_code == 200:
        modelos = respuesta.json().get("models", [])
        return "VALE", f"{len(modelos)} modelos visibles"

    try:
        error = respuesta.json().get("error", {})
        detalles = error.get("details") or [{}]
        razon = detalles[0].get("reason") or error.get("status") or "?"
    except ValueError:
        razon = respuesta.text[:80]

    if respuesta.status_code >= 500:
        # No culpa a la clave de algo que es de Google: si aqui se leyera
        # "la clave no vale" se tiraria una credencial buena.
        return "GOOGLE MAL", f"HTTP {respuesta.status_code}, {razon}"
    return "NO VALE", f"HTTP {respuesta.status_code}, {razon}"


def main() -> int:
    if sys.stdin.isatty():
        print(__doc__)
        print("Falta la entrada. Prueba:")
        print("  docker compose exec -T api python scripts/check_keys.py < .env")
        return 2

    claves = leer_claves(sys.stdin.read())
    if not claves:
        print("No se encontro ninguna linea GEMINI_API_KEY en la entrada.")
        return 2

    print("Comprobando contra ListModels. Esto NO gasta cupo de generacion.")
    print()
    print("cuenta".ljust(14) + "huella".ljust(15) + "".ljust(9) + "veredicto")
    print("-" * 78)

    validas = 0
    for etiqueta, clave, activa in claves:
        veredicto, detalle = comprobar(clave)
        validas += veredicto == "VALE"
        marca = "ACTIVA" if activa else ""
        print(
            etiqueta[:13].ljust(14)
            + account_fingerprint(clave).ljust(15)
            + marca.ljust(9)
            + f"{veredicto:<12}{detalle}"
        )

    print()
    if validas:
        print(f"{validas} de {len(claves)} valen. Si la que vale no es la ACTIVA,")
        print("hay que moverla en .env y REHACER el contenedor: la variable se")
        print("inyecta al crearlo, editar el fichero no basta.")
        print("  docker compose up -d --force-recreate api")
    else:
        print("Ninguna vale. Sacar una nueva en aistudio.google.com/apikey,")
        print("ponerla en .env y rehacer el contenedor.")
        print()
        print("Ojo con el diagnostico: esto no dice nada sobre si Gemini")
        print("responde hoy, ni gasta cupo. Son tres cosas distintas.")
    return 0 if validas else 1


if __name__ == "__main__":
    sys.exit(main())
