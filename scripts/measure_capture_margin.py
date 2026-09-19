"""Cuanto aguanta la lectura antes de romperse: el margen de una captura.

POR QUE EXISTE
--------------

La pantalla acepta fotos de camara, y todos los numeros de lectura que
publica este proyecto -46/70 de MRZ exacta, 58-60/70 en campos del
anverso- estan medidos sobre imagenes sinteticas, no sobre fotografias.
Antes de grabar una demostracion conviene saber por donde se rompe la
lectura, porque enterarse con la camara ya encendida sale caro.

Esto no sustituye a una foto real. Una fotografia de un papel impreso
tiene gradientes de luz, perspectiva y trama de impresion que la
degradacion sintetica no modela, y decir lo contrario seria justo el tipo
de numero sin procedencia que este proyecto no se permite.

Lo que si da es la **frontera**: a partir de que desenfoque, de que
reduccion de tamano, de que reflejo y de que giro deja de cuadrar la MRZ o
de coincidir un cotejo. Eso se traduce en instrucciones que si sirven
delante de la camara: cuanta luz, cuanto pulso y cuanto acercarse.

    docker compose exec api python scripts/measure_capture_margin.py
"""

from __future__ import annotations

import argparse
import sys

from app.evaluation.catalog import person
from app.signals.pipeline import build_signals
from app.synthetic.cedula import render_back, render_front
from app.synthetic.degradation import blur, downscale, glare, rotate

ANCHO = 78

# Lo que hay que mirar para decir si una captura sirve. La MRZ es la que
# manda: sus digitos de control o cuadran o no, sin termino medio, y
# cuando dejan de cuadrar el sistema pierde su unica senal dura.
CLAVES = (
    "mrz.readable",
    "mrz.checks_ok",
    "mrz.repaired",
    "cross.nuip",
    "cross.birth_date",
    "ocr.nuip",
    "ocr.birth_date",
)


def lee(anverso, reverso) -> dict:
    senales = build_signals(anverso, reverso)
    salida = {}
    for clave in CLAVES:
        senal = senales.get(clave)
        salida[clave] = senal.value if senal and senal.available else None
    nitidez = senales.get("quality.front_sharpness")
    salida["nitidez"] = nitidez.value if nitidez and nitidez.available else None
    return salida


def sirve(lectura: dict, verdad) -> bool:
    """Una captura sirve si lo leido es CORRECTO, no solo coherente.

    La primera version de esta funcion se conformaba con que los digitos
    de control cuadraran y los cotejos coincidieran. Eso es coherencia
    interna, y este proyecto ya sabe lo que vale: el trabajo sobre la MRZ
    bajo los falsos validos de 52/70 a 7/70 precisamente porque una MRZ
    mal leida puede seguir cuadrando consigo misma.

    Como el documento se fabrica aqui, la verdad esta disponible y no hay
    excusa para no compararla. Se comprueba contra el NUIP y la fecha de
    nacimiento reales, que es lo que un cotejo no puede inventarse.
    """
    if lectura["mrz.checks_ok"] is not True:
        return False
    if lectura["cross.nuip"] != "match" or lectura["cross.birth_date"] != "match":
        return False

    # Se quitan TODOS los separadores y no solo el punto. La cedula lo
    # imprime con puntos de millar, pero el OCR devuelve a veces una coma
    # donde hay un punto, y eso no es una cifra mal leida: son los mismos
    # diez digitos con otro adorno. Comparar sin normalizar eso daba por
    # ilegible una captura que se habia leido entera.
    nuip_leido = "".join(c for c in (lectura["ocr.nuip"] or "") if c.isdigit())
    if nuip_leido != verdad.nuip:
        return False

    # El OCR devuelve la fecha como la imprime la cedula ("15 ABR 2004") y
    # a veces se traga el campo de al lado. Basta con que el dia, el mes y
    # el ano esten, en ese orden, para dar la lectura por buena.
    MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
             "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
    esperada = (f"{verdad.birth_date.day:02d} "
                f"{MESES[verdad.birth_date.month - 1]} {verdad.birth_date.year}")
    return esperada in (lectura["ocr.birth_date"] or "")


def barrido(nombre, valores, aplicar, anverso, reverso, verdad) -> tuple:
    """Recorre una degradacion creciente y dice donde deja de ser fiable.

    Devuelve dos cosas, y la distincion importa: el ultimo valor **antes
    del primer fallo** -hasta ahi se lee siempre- y el ultimo valor que
    acerto alguna vez. Cuando no coinciden, en medio hay una zona en la
    que a veces se lee y a veces no, y prometer el segundo numero seria
    vender como margen lo que es suerte.
    """
    print()
    print(nombre)
    print("-" * ANCHO)
    fiable_hasta = None
    ultimo_acierto = None
    ya_fallo = False

    for valor in valores:
        a = aplicar(anverso, valor)
        b = aplicar(reverso, valor)
        lectura = lee(a, b)
        ok = sirve(lectura, verdad)
        if ok:
            ultimo_acierto = valor
            if not ya_fallo:
                fiable_hasta = valor
        else:
            ya_fallo = True

        mrz = "cuadra" if lectura["mrz.checks_ok"] else (
            "ilegible" if lectura["mrz.readable"] is not True else "digitos mal"
        )
        nitidez = lectura["nitidez"]
        print(
            f"  {valor:>8}  {'lee bien' if ok else 'NO':<9} MRZ {mrz:<12} "
            f"nuip {str(lectura['cross.nuip']):<9} "
            f"nitidez {nitidez if nitidez is not None else '-'}"
        )

    return fiable_hasta, ultimo_acierto


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rapido",
        action="store_true",
        help="menos pasos por barrido; para comprobar que el guion corre",
    )
    args = parser.parse_args()

    datos = person()
    anverso, reverso = render_front(datos), render_back(datos)

    base = lee(anverso, reverso)
    print("=" * ANCHO)
    print("MARGEN DE UNA CAPTURA")
    print("=" * ANCHO)
    print(f"  Documento limpio: {'se lee entero' if sirve(base, datos) else 'NO SE LEE'}")
    print(f"  Nitidez de referencia: {base['nitidez']}")
    print(f"  Tamano original: {anverso.size[0]}x{anverso.size[1]}")
    if not sirve(base, datos):
        print("  El documento limpio ya no se lee. Algo esta roto antes de degradar.")
        return 1

    desenfoques = [0.5, 1.0, 1.5, 2.0] if args.rapido else [
        0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0
    ]
    reducciones = [1.5, 2.0, 3.0] if args.rapido else [1.25, 1.5, 2.0, 2.5, 3.0, 4.0]
    giros = [2.0, 5.0, 10.0] if args.rapido else [1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0]

    desenfoque = barrido(
        "DESENFOQUE — el pulso y el enfoque de la camara (radio en pixeles)",
        desenfoques, lambda im, v: blur(im, radius=v), anverso, reverso, datos,
    )
    reduccion = barrido(
        "DISTANCIA — cuanto se encoge el documento en el encuadre (factor)",
        reducciones, lambda im, v: downscale(im, factor=v), anverso, reverso, datos,
    )
    giro = barrido(
        "GIRO — el documento torcido respecto a la camara (grados)",
        giros, lambda im, v: rotate(im, degrees=v), anverso, reverso, datos,
    )

    print()
    print("=" * ANCHO)
    print("LO QUE ESTO SIGNIFICA DELANTE DE UNA CAMARA")
    print("=" * ANCHO)
    for etiqueta, (fiable, ultimo), unidad in (
        ("Desenfoque", desenfoque, "de radio"),
        ("Distancia", reduccion, "de reduccion"),
        ("Giro", giro, "grados"),
    ):
        print(f"  {etiqueta:<12} fiable hasta {fiable} {unidad}", end="")
        if ultimo is not None and fiable is not None and ultimo != fiable:
            print(f"; entre {fiable} y {ultimo} acierta a veces y a veces no")
        else:
            print()
    if reduccion[0]:
        print(f"               el documento a {anverso.size[0] / reduccion[0]:.0f} px"
              f" de ancho sobre {anverso.size[0]} originales")
    print()
    print("  Dos advertencias, y las dos importan.")
    print()
    print("  La lectura no se degrada suavemente, salta. Hay valores mayores")
    print("  que aciertan y valores menores que fallan, porque un campo se")
    print("  come al de al lado y eso tumba los digitos de control. Por eso")
    print("  se informa 'fiable hasta' y no 'maximo que aguanta': lo segundo")
    print("  vende como margen lo que es suerte.")
    print()
    print("  Y esto es la frontera de la degradacion SINTETICA. Una foto de un")
    print("  papel impreso trae ademas gradientes de luz, perspectiva y trama")
    print("  de impresion, que no estan modelados aqui y solo pueden empeorar")
    print("  el resultado. Sirve para saber por donde se rompe, no para")
    print("  prometer que leera.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
