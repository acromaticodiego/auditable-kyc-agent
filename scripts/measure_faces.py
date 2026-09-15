"""Mide la senal facial sobre las fotos reales de `data/real/caras/`.

No gasta cupo de la API: los modelos son locales.

QUE SE MIDE Y POR QUE ASI
-------------------------

La pregunta no es si ArcFace funciona -- eso ya lo midieron sus autores --
sino si funciona **despues de pasar por este sistema**: la foto se encoge
al hueco del retrato de la cedula, se imprime, alguien la fotografia con un
movil y llega comprimida, movida o con un reflejo encima.  Esa cadena puede
destruir un embedding y no habria forma de saberlo midiendo las fotos
crudas.

Por eso se mide dos veces:

- **crudo**: foto contra foto, que es el techo del reconocedor;
- **por el documento**: la foto pintada en la cedula contra la selfie, que
  es lo que el sistema hace de verdad, y ademas con la cedula degradada.

La distancia entre esos dos numeros es lo que cuesta el documento.

LA ASIMETRIA DE LA MUESTRA, DICHA ANTES QUE LOS NUMEROS
-------------------------------------------------------

El material disponible tiene un par genuino y unos mil impostores, porque
llego asi: una sola persona aporto dos fotos suyas y las demas una.  Eso
hace que los dos lados de la medida no valgan lo mismo:

- el lado **impostor** esta bien medido y es el que fija el umbral por
  seguridad: cuanto se parecen dos personas distintas como maximo;
- el lado **genuino** tiene n=1. Sirve para ver si hay separacion y no
  sirve para estimar a cuantos clientes legitimos se rechazaria, que es un
  numero que este proyecto NO tiene.

    docker compose exec api python scripts/measure_faces.py
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from app.evaluation.catalog import person
from app.signals.face import FaceReader, FaceReading
from app.synthetic.cedula import render_front
from app.synthetic.degradation import blur, jpeg_artifacts

CARAS = Path("data/real/caras")
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
LADO_MINIMO = 100


def fotos_por_identidad() -> dict[str, list[Path]]:
    identidades: dict[str, list[Path]] = {}
    for carpeta in sorted(
        p for p in CARAS.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        imagenes = sorted(
            f for f in carpeta.iterdir() if f.suffix.lower() in EXTENSIONES
        )
        if imagenes:
            identidades[carpeta.name] = imagenes
    return identidades


def resumir(nombre: str, valores: list[float]) -> None:
    if not valores:
        print(f"  {nombre:28} (ninguno)")
        return
    datos = np.array(valores)
    print(
        f"  {nombre:28} n={len(datos):4}  "
        f"min {datos.min():7.4f}  media {datos.mean():7.4f}  "
        f"max {datos.max():7.4f}"
    )


def main() -> int:
    if not CARAS.exists():
        print(f"No existe {CARAS}. Ver data/real/caras/LEEME.md")
        return 1

    identidades = fotos_por_identidad()
    if len(identidades) < 2:
        print("Hacen falta al menos dos identidades para formar un par.")
        return 1

    lector = FaceReader()
    datos_cedula = person()

    print(f"Leyendo {sum(len(v) for v in identidades.values())} fotos de "
          f"{len(identidades)} identidades...\n")

    # Embedding de cada foto cruda, y de la primera foto de cada identidad
    # pintada en una cedula (limpia y degradada).
    crudos: dict[str, list[FaceReading]] = {}
    en_cedula: dict[str, FaceReading] = {}
    en_cedula_degradada: dict[str, FaceReading] = {}
    descartadas = 0

    for identidad, imagenes in identidades.items():
        lecturas = []
        for imagen in imagenes:
            lectura = lector.read(imagen)
            if lectura.available and lectura.side >= LADO_MINIMO:
                lecturas.append(lectura)
            else:
                descartadas += 1
        if not lecturas:
            continue
        crudos[identidad] = lecturas

        retrato = Image.open(imagenes[0])
        anverso = render_front(datos_cedula, portrait=retrato)
        en_cedula[identidad] = lector.read(anverso, allow_ghost=True)

        # Degradacion tipica de una foto de cedula enviada por mensajeria.
        degradado = jpeg_artifacts(blur(anverso, radius=1.0), quality=45)
        en_cedula_degradada[identidad] = lector.read(degradado, allow_ghost=True)

    utiles = list(crudos)
    print(f"identidades utiles: {len(utiles)}   fotos descartadas: {descartadas}")
    sin_cara_en_cedula = [i for i in utiles if not en_cedula[i].available]
    if sin_cara_en_cedula:
        print(
            f"en {len(sin_cara_en_cedula)} cedulas no se detecto la cara del "
            f"retrato: {', '.join(sin_cara_en_cedula)}"
        )

    def punto(a: FaceReading, b: FaceReading) -> float | None:
        if not a.available or not b.available:
            return None
        return float(np.dot(a.embedding, b.embedding))

    # --- genuinos: la misma persona, dos fotos distintas ---
    genuino_crudo: list[float] = []
    genuino_cedula: list[float] = []
    genuino_degradado: list[float] = []
    detalle_genuinos: list[tuple[str, float, float, float]] = []

    for identidad, lecturas in crudos.items():
        if len(lecturas) < 2:
            continue
        for primera, segunda in itertools.combinations(range(len(lecturas)), 2):
            a, b = lecturas[primera], lecturas[segunda]
            crudo = punto(a, b)
            if crudo is not None:
                genuino_crudo.append(crudo)
            # La cedula se pinto con la PRIMERA foto, asi que se compara
            # contra la segunda: comparar contra la misma que se pinto seria
            # medir el renderizado y llamarlo reconocimiento.
            cedula = punto(en_cedula[identidad], b)
            degradada = punto(en_cedula_degradada[identidad], b)
            if cedula is not None and degradada is not None:
                genuino_cedula.append(cedula)
                genuino_degradado.append(degradada)
                detalle_genuinos.append((identidad, crudo, cedula, degradada))

    # --- impostores: personas distintas ---
    impostor_crudo: list[float] = []
    impostor_cedula: list[float] = []
    impostor_degradado: list[float] = []

    for uno, otro in itertools.permutations(utiles, 2):
        for lectura in crudos[otro]:
            valor = punto(en_cedula[uno], lectura)
            if valor is not None:
                impostor_cedula.append(valor)
            valor = punto(en_cedula_degradada[uno], lectura)
            if valor is not None:
                impostor_degradado.append(valor)
    for uno, otro in itertools.combinations(utiles, 2):
        for a in crudos[uno]:
            for b in crudos[otro]:
                valor = punto(a, b)
                if valor is not None:
                    impostor_crudo.append(valor)

    print(f"\n{'=' * 74}")
    print("SIMILITUD ENTRE CARAS")
    print("=" * 74)
    print("\nMisma persona (pares genuinos):")
    resumir("foto contra foto", genuino_crudo)
    resumir("cedula limpia contra selfie", genuino_cedula)
    resumir("cedula degradada contra selfie", genuino_degradado)
    for identidad, crudo, cedula, degradada in detalle_genuinos:
        print(
            f"    {identidad}: crudo {crudo:.4f}, por cedula {cedula:.4f}, "
            f"degradada {degradada:.4f}"
        )

    print("\nPersonas distintas (pares impostores):")
    resumir("foto contra foto", impostor_crudo)
    resumir("cedula limpia contra selfie", impostor_cedula)
    resumir("cedula degradada contra selfie", impostor_degradado)

    print(f"\n{'=' * 74}")
    print("SEPARACION")
    print("=" * 74)
    for nombre, gen, imp in (
        ("foto contra foto", genuino_crudo, impostor_crudo),
        ("por el documento", genuino_cedula, impostor_cedula),
        ("documento degradado", genuino_degradado, impostor_degradado),
    ):
        if not gen or not imp:
            continue
        peor_genuino = min(gen)
        peor_impostor = max(imp)
        hueco = peor_genuino - peor_impostor
        veredicto = "se separan" if hueco > 0 else "SE SOLAPAN"
        print(
            f"  {nombre:22} genuino mas bajo {peor_genuino:7.4f}  "
            f"impostor mas alto {peor_impostor:7.4f}  "
            f"hueco {hueco:+.4f}  {veredicto}"
        )

    print(f"\n{'=' * 74}")
    print("LO QUE ESTOS NUMEROS NO DICEN")
    print("=" * 74)
    print(
        f"  Los pares genuinos son {len(genuino_cedula)}. Con esa muestra se puede\n"
        "  ver si hay separacion y NO se puede estimar a cuantos clientes\n"
        "  legitimos rechazaria un umbral: para eso harian falta muchas\n"
        "  personas fotografiadas dos veces, en dias y luces distintos.\n"
        f"\n  Los pares impostores son {len(impostor_cedula)} y vienen de "
        f"{len(utiles)} personas\n"
        "  reales, asi que el lado que dice cuanto se parecen dos desconocidos\n"
        "  si esta medido.\n"
        "\n  Todas son fotos de conocidos, hechas con moviles: no representan\n"
        "  la variedad de edad, tono de piel ni condiciones de captura que\n"
        "  tendria un sistema en produccion."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
