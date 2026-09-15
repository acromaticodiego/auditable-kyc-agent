"""Reparte las fotos sueltas en una carpeta por persona.

Existe para quitar la unica friccion real de aportar el material: crear
veinticinco carpetas y arrastrar cincuenta ficheros a mano invita a
equivocarse y a dejarlo a medias.

CADA FOTO ES UNA PERSONA DISTINTA, SALVO QUE SE DIGA LO CONTRARIO
-----------------------------------------------------------------

Por defecto **una foto es una persona**.  Puede parecer excesivamente
cauto, pero el error contrario no se ve y falsea la medida entera: si dos
fotos de personas distintas acaban en la misma carpeta, el conjunto pasa a
tener un par "genuino" que en realidad es un impostor, y entonces la medida
dice justo lo contrario de lo que pasa.

En este proyecto el material llego asi: veintitres fotos tipo documento y
unas cuantas selfies, **de personas distintas**.  Emparejar `documento7`
con `selfie7` por compartir numero habria construido pares genuinos falsos
sin que nada fallara por ningun lado.

Cuando de verdad haya dos fotos de la misma persona, se juntan con
`--emparejar-por-numero`, que las agrupa por el numero del nombre. Esa
opcion existe para el dia que haya material que lo permita, y esta apagada
a proposito.

Emparejar por parecido facial no se hace nunca: seria usar el modelo que
queremos medir para construir el conjunto con el que medirlo.

    docker compose exec api python scripts/organize_faces.py
    docker compose exec api python scripts/organize_faces.py --hazlo
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

CARAS = Path("data/real/caras")
ENTRADA = CARAS / "_sin_clasificar"
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# `documento7.jpg`, `selfie7.png`, `documento.jpg18.gif`, `documento.jpg`.
# El \D* del medio absorbe la basura que a veces queda entre el papel y el
# numero, como el `.jpg` de un fichero renombrado a medias.
PATRON = re.compile(r"^(documento|selfie)\D*(\d*)", re.IGNORECASE)


def clasificar(foto: Path) -> tuple[str, str] | None:
    """Devuelve (papel, numero) o None si el nombre no dice nada."""
    encontrado = PATRON.match(foto.name)
    if encontrado is None:
        return None
    papel = encontrado.group(1).lower()
    numero = encontrado.group(2) or "0"
    return papel, numero


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hazlo",
        action="store_true",
        help="mueve los ficheros de verdad. Sin esto solo enseña que haria.",
    )
    parser.add_argument(
        "--emparejar-por-numero",
        action="store_true",
        help=(
            "junta documentoN y selfieN como la MISMA persona. Solo si de "
            "verdad lo son: por defecto cada foto es una persona distinta."
        ),
    )
    args = parser.parse_args()

    if not ENTRADA.exists():
        print(f"No existe {ENTRADA}. Crearla y soltar las fotos dentro.")
        return 1

    fotos = sorted(
        p for p in ENTRADA.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES
    )
    if not fotos:
        print(f"No hay ninguna foto en {ENTRADA}.")
        return 1

    grupos: dict[str, dict[str, Path]] = {}
    sin_clasificar: list[Path] = []
    for foto in fotos:
        clasificada = clasificar(foto)
        if clasificada is None:
            # Un nombre que no dice si es documento o selfie sigue siendo una
            # persona: para los pares impostores vale igual.
            papel, numero = "foto", foto.stem
        else:
            papel, numero = clasificada

        # La clave decide que acaba junto. Sin --emparejar-por-numero se
        # incluye el papel, de modo que documento7 y selfie7 caen en
        # personas distintas.
        clave = numero if args.emparejar_por_numero else f"{papel}-{numero}"
        grupos.setdefault(clave, {})[papel] = foto

    def orden_de(clave: str) -> tuple[str, int]:
        papel, _, numero = clave.rpartition("-")
        return (papel, int(numero)) if numero.isdigit() else (clave, 0)

    orden = sorted(grupos, key=orden_de)
    movimientos: list[tuple[Path, Path]] = []
    con_pareja = 0

    print(f"{len(fotos)} fotos -> {len(grupos)} personas\n")
    for indice, numero in enumerate(orden, start=1):
        carpeta = CARAS / f"persona-{indice:02d}"
        piezas = grupos[numero]
        etiqueta = "par" if len(piezas) == 2 else "sola"
        con_pareja += len(piezas) == 2

        detalle = ", ".join(p.name for _, p in sorted(piezas.items()))
        print(f"  persona-{indice:02d}  [{etiqueta:4}] {detalle}")

        for papel, origen in piezas.items():
            movimientos.append((origen, carpeta / f"{papel}{origen.suffix.lower()}"))

    solas = len(grupos) - con_pareja
    print(f"\n  {con_pareja} personas con dos o mas fotos (dan par genuino)")
    if solas:
        print(f"  {solas} con una sola foto (solo sirven para pares impostores)")
    if con_pareja == 0:
        print(
            "\n  SIN PARES GENUINOS. Se podra medir donde el sistema confunde a"
            "\n  dos personas distintas, que es el lado que fija el umbral por"
            "\n  seguridad, pero NO a cuantos clientes legitimos rechazaria."
        )
    if sin_clasificar:
        print(
            f"\n  {len(sin_clasificar)} ficheros con un nombre que no dice si es "
            "documento o selfie, y se quedan sin mover:"
        )
        for foto in sin_clasificar:
            print(f"    {foto.name}")

    if not args.hazlo:
        print(
            "\nNo se ha movido nada. Revisa el reparto: dos fotos acaban juntas\n"
            "solo si comparten numero en el nombre. Si alguna pareja esta mal,\n"
            "se arregla renombrando antes de seguir.\n\nSi esta bien: --hazlo"
        )
        return 0

    for origen, destino in movimientos:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(destino))

    print(f"\n{len(movimientos)} fotos colocadas en {len(grupos)} carpetas.")
    print("Ahora: docker compose exec api python scripts/check_faces.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
