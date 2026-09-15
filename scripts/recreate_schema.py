"""Borra las tablas y las vuelve a crear.  **Destruye todo lo guardado.**

Esta es la factura de no tener Alembic, y conviene verla escrita en vez de
descubrirla.  `ensure_schema()` usa `create_all`, que crea las tablas que
faltan y **no toca las que ya existen**: anadir una columna al modelo no
cambia nada en una base de datos que ya tiene esa tabla, y lo peor es que no
falla -- la aplicacion arranca tan campante y revienta despues, al insertar,
con un error de columna inexistente.

Mientras las tablas solo guarden verificaciones de prueba, recrearlas es un
mal menor y este script lo hace explicito.  **En cuanto haya una sola
decision que no se pueda perder, esto deja de ser aceptable y el proyecto
necesita migraciones de verdad.**  Ese es el momento de meter Alembic, no
antes y desde luego no despues.

Pide confirmacion a proposito: un script que borra tablas y se ejecuta sin
preguntar acaba ejecutandose contra lo que no debia.

    docker compose exec api python scripts/recreate_schema.py --si-borrar-todo
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from app.db import engine
from app.storage.models import ensure_schema, metadata


def contar() -> dict[str, int]:
    """Cuantas filas hay en cada tabla, para que nadie borre a ciegas."""
    totales: dict[str, int] = {}
    with engine.connect() as conexion:
        for tabla in metadata.sorted_tables:
            try:
                totales[tabla.name] = conexion.execute(
                    sa.select(sa.func.count()).select_from(tabla)
                ).scalar_one()
            except sa.exc.SQLAlchemyError:
                # La tabla aun no existe: no es un error, es el caso normal
                # la primera vez que se ejecuta esto.
                totales[tabla.name] = 0
    return totales


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--si-borrar-todo",
        action="store_true",
        help="confirma que se puede perder lo guardado. Sin esto no borra nada.",
    )
    args = parser.parse_args()

    totales = contar()
    print("Filas actuales:")
    for nombre, cuantas in totales.items():
        print(f"  {nombre:28} {cuantas}")

    if not args.si_borrar_todo:
        print(
            "\nNo se ha borrado nada. Para recrear el esquema hay que pasar\n"
            "--si-borrar-todo, y eso significa perder las filas de arriba."
        )
        return 1

    print("\nBorrando y recreando...")
    metadata.drop_all(engine)
    ensure_schema(engine)
    print("Esquema recreado. Todo lo que hubiera guardado ya no esta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
