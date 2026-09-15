"""Comprueba que las fotos de `data/real/caras/` sirvan para medir.

Los fallos que estropean una medida facial no dan error, dan un numero.  Si
en una foto no se detecta ninguna cara, ese par desaparece de la muestra sin
que nadie lo note y el tamano real deja de ser el que se publica.  Si la
misma foto esta repetida en dos carpetas, un par impostor es en realidad
genuino y ensucia justo el lado que decide el umbral.

Este script tambien dice **que se va a poder medir y que no**, que es la
pregunta importante cuando solo hay una foto por persona:

- con una foto por cabeza salen muchos pares **impostores** y **ningun par
  genuino**, asi que se puede medir donde el sistema confundiria a dos
  personas y no se puede medir si reconoce a la misma en otro momento;
- cada persona con dos o mas fotos aporta pares genuinos.

No gasta cupo de la API: los modelos son locales.

    docker compose exec api python scripts/check_faces.py
"""

from __future__ import annotations

import hashlib
import itertools
import sys
from pathlib import Path

CARAS = Path("data/real/caras")
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Por debajo de esto la cara tiene tan pocos pixeles que el embedding deja de
# ser comparable: ArcFace trabaja sobre recortes de 112x112, asi que una cara
# de 80 pixeles se esta ampliando para alimentarlo.
LADO_MINIMO = 100
LADO_COMODO = 200

# Dos fotos distintas de la misma persona en sesiones distintas rara vez
# pasan de aqui.  Por encima, lo mas probable es que sean la misma toma
# recortada de dos maneras, y eso mide el recorte y no la identidad.
SIMILITUD_SOSPECHOSA = 0.98


def fotos_de(carpeta: Path) -> list[Path]:
    return sorted(
        p for p in carpeta.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES
    )


def main() -> int:
    if not CARAS.exists():
        print(f"No existe {CARAS}.")
        return 1

    personas = sorted(
        p for p in CARAS.iterdir() if p.is_dir() and not p.name.startswith("_")
    )
    sueltas = CARAS / "_sin_clasificar"
    pendientes = fotos_de(sueltas) if sueltas.exists() else []

    if not personas:
        print(f"No hay ninguna carpeta de persona en {CARAS}.")
        if pendientes:
            print(
                f"\nHay {len(pendientes)} fotos en {sueltas.name}/ sin repartir.\n"
                "Reparte con: docker compose exec api python "
                "scripts/organize_faces.py"
            )
        return 1

    # Se importa aqui y no arriba para que los avisos de arriba salgan al
    # instante en vez de despues de cargar 200 MB de modelos.
    from app.signals.face import FaceReader

    lector = FaceReader()
    print(f"Comprobando {len(personas)} personas en {CARAS}\n")

    hashes: dict[str, list[str]] = {}
    utiles: dict[str, int] = {}
    problemas: list[str] = []

    for persona in personas:
        nombre = persona.name
        imagenes = fotos_de(persona)

        if not imagenes:
            print(f"{nombre:14} (vacia)")
            continue

        buenas = 0
        lecturas = []
        for imagen in imagenes:
            hashes.setdefault(
                hashlib.sha256(imagen.read_bytes()).hexdigest(), []
            ).append(f"{nombre}/{imagen.name}")

            lectura = lector.read(imagen)
            if not lectura.available:
                print(f"{nombre:14} {imagen.name:18} {lectura.reason}")
                problemas.append(f"{nombre}/{imagen.name}: {lectura.reason}")
                continue
            if lectura.side < LADO_MINIMO:
                print(
                    f"{nombre:14} {imagen.name:18} la cara mide "
                    f"{lectura.side} px y el minimo son {LADO_MINIMO}"
                )
                problemas.append(f"{nombre}/{imagen.name}: cara demasiado pequena")
                continue

            aviso = "" if lectura.side >= LADO_COMODO else "  (cara justa)"
            print(
                f"{nombre:14} {imagen.name:18} ok  cara {lectura.side} px, "
                f"confianza {lectura.confidence:.2f}{aviso}"
            )
            lecturas.append(lectura)
            buenas += 1

        # Si la persona tiene varias fotos utiles, se mira si de verdad son
        # tomas distintas antes de contarlas como par genuino.
        for (una, otra) in itertools.combinations(lecturas, 2):
            from app.signals.face import similarity

            parecido = similarity(una, otra)
            if parecido is not None and parecido > SIMILITUD_SOSPECHOSA:
                print(
                    f"{'':14} aviso: dos de sus fotos se parecen {parecido:.3f}, "
                    "sospechoso de ser la misma toma"
                )
                problemas.append(
                    f"{nombre}: dos fotos casi identicas ({parecido:.3f})"
                )

        if buenas:
            utiles[nombre] = buenas

    repetidos = {h: rutas for h, rutas in hashes.items() if len(rutas) > 1}
    if repetidos:
        print("\nHay ficheros repetidos:")
        for rutas in repetidos.values():
            print(f"  {' == '.join(rutas)}")
        problemas.append("la misma foto aparece mas de una vez")

    identidades = len(utiles)
    genuinos = sum(n * (n - 1) // 2 for n in utiles.values())
    impostores = sum(
        a * b for a, b in itertools.combinations(utiles.values(), 2)
    )

    print(f"\n{'=' * 70}")
    print(f"identidades con al menos una foto util : {identidades}")
    print(f"fotos utiles en total                  : {sum(utiles.values())}")
    print(f"pares genuinos (misma persona)         : {genuinos}")
    print(f"pares impostores (personas distintas)  : {impostores}")

    if genuinos == 0:
        print(
            "\nSIN PARES GENUINOS. Con una sola foto por persona se puede medir\n"
            "donde el sistema confundiria a dos personas distintas, que es el\n"
            "lado que fija el umbral por seguridad, pero NO si reconoce a la\n"
            "misma persona en otra foto, que es el lado que decide a cuantos\n"
            "clientes legitimos se rechaza.\n"
            "\nUna segunda foto de aunque sean tres o cuatro de esas personas,\n"
            "hecha en otro momento, ya permite ver los dos lados. Se mete\n"
            "dentro de su carpeta con cualquier nombre."
        )
    elif genuinos < 5:
        print(
            f"\nSolo {genuinos} pares genuinos: se puede mirar, pero cualquier\n"
            "umbral elegido con tan pocos puntos es una conjetura con formato\n"
            "de numero."
        )

    if pendientes:
        print(f"\nQuedan {len(pendientes)} fotos sin repartir en {sueltas.name}/")

    if problemas:
        print("\nPendiente de arreglar:")
        for problema in problemas:
            print(f"  - {problema}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
