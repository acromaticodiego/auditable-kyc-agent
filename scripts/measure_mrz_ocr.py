"""Mide cuanto acierta la lectura de la MRZ, y cuando se equivoca callando.

La MRZ es el unico sitio del documento donde se puede saber si el OCR
acerto sin tener la respuesta delante, porque trae digitos de control.
Aqui si tenemos la respuesta -- las cedulas son sinteticas y sabemos que
pusimos -- asi que se puede medir algo que en produccion no se ve:

**cuantas veces los digitos de control dicen "correcto" sobre una lectura
que en realidad esta mal.**

Ese numero importa mas que la tasa de acierto. Una lectura incorrecta que
se declara a si misma valida es la que se cuela hasta la decision.

    docker compose exec api python scripts/measure_mrz_ocr.py
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date

from app.signals.mrz import parse_mrz
from app.signals.ocr_mrz import read_mrz
from app.synthetic.cedula import CedulaData, render_back
from app.synthetic.degradation import blur, downscale, jpeg_artifacts

IDENTITIES = [
    ("WALTEROS", "LAURA", "1234567890", "000000012", date(2004, 4, 15), date(2032, 4, 19)),
    ("OSSA DORIA", "JUAN DIEGO", "1020483236", "087258257", date(1998, 1, 14), date(2036, 1, 8)),
    ("GOMEZ RUIZ", "ANA MARIA", "1098765432", "045612378", date(1991, 11, 3), date(2029, 6, 30)),
    ("PEREZ", "CARLOS ANDRES", "8123456", "912345678", date(1976, 2, 29), date(2027, 12, 1)),
    ("ZAPATA MEJIA", "SOFIA", "1152003344", "600112233", date(2000, 7, 21), date(2035, 7, 20)),
    ("QUINTERO", "JOSE LUIS", "7654321", "123456789", date(1968, 9, 9), date(2028, 3, 15)),
    ("BEDOYA SUAREZ", "VALENTINA", "1017889900", "334455667", date(2003, 12, 25), date(2033, 12, 24)),
    ("SIERRA", "SANTIAGO", "1090011223", "778899001", date(1995, 5, 5), date(2031, 5, 4)),
    ("ARANGO LOPEZ", "ISABELLA", "1128899776", "556677889", date(2007, 8, 18), date(2034, 8, 17)),
    ("MONTOYA", "ESTEBAN", "71234567", "998877665", date(1983, 3, 30), date(2026, 10, 10)),
]

CONDITIONS = {
    "limpia": lambda image: image,
    "jpeg q=60": lambda image: jpeg_artifacts(image, 60),
    "jpeg q=20": lambda image: jpeg_artifacts(image, 20),
    "blur r=1.5": lambda image: blur(image, 1.5),
    "blur r=3": lambda image: blur(image, 3.0),
    "resolucion /2": lambda image: downscale(image, 2.0),
    "resolucion /3": lambda image: downscale(image, 3.0),
}


def build(identity) -> CedulaData:
    surnames, given, nuip, number, birth, expiry = identity
    return CedulaData(
        nuip=nuip,
        document_number=number,
        surnames=surnames,
        given_names=given,
        birth_date=birth,
        birth_place="MEDELLIN (ANTIOQUIA)",
        sex="F",
        height_m=1.70,
        blood_group="O+",
        issue_date=date(2022, 1, 1),
        expiry_date=expiry,
        issue_place="MEDELLIN",
    )


def main() -> int:
    totals: Counter[str] = Counter()
    per_condition: dict[str, Counter[str]] = {name: Counter() for name in CONDITIONS}
    silent_failures: list[tuple[str, str, str, str]] = []

    for identity in IDENTITIES:
        data = build(identity)
        truth = data.mrz()
        back = render_back(data)

        for name, degrade in CONDITIONS.items():
            reading = read_mrz(degrade(back))
            bucket = per_condition[name]
            totals["lecturas"] += 1
            bucket["lecturas"] += 1

            if not reading.ok:
                totals["sin_mrz"] += 1
                bucket["sin_mrz"] += 1
                continue

            exact = reading.lines == truth
            parsed = reading.parsed()
            checks_ok = bool(parsed and parsed.checks_ok)

            if exact:
                totals["exactas"] += 1
                bucket["exactas"] += 1
            if checks_ok:
                totals["checks_ok"] += 1
                bucket["checks_ok"] += 1
            if reading.repaired:
                totals["reparadas"] += 1
                bucket["reparadas"] += 1

            # El caso peligroso: la aritmetica aprueba una lectura erronea.
            if checks_ok and not exact:
                totals["falso_ok"] += 1
                bucket["falso_ok"] += 1
                for real, leido in zip(truth, reading.lines):
                    if real != leido:
                        silent_failures.append((identity[0], name, real, leido))
                        break

    print(f"{len(IDENTITIES)} identidades x {len(CONDITIONS)} condiciones = "
          f"{totals['lecturas']} lecturas\n")
    print(f"{'condicion':16} {'n':>3} {'exactas':>9} {'checks ok':>10} "
          f"{'reparadas':>10} {'FALSO OK':>9} {'sin MRZ':>8}")
    for name in CONDITIONS:
        b = per_condition[name]
        n = b["lecturas"]
        print(f"{name:16} {n:3} {b['exactas']:6}/{n:<3} {b['checks_ok']:7}/{n:<3} "
              f"{b['reparadas']:10} {b['falso_ok']:9} {b['sin_mrz']:8}")

    n = totals["lecturas"]
    print(f"\n{'TOTAL':16} {n:3} {totals['exactas']:6}/{n:<3} "
          f"{totals['checks_ok']:7}/{n:<3} {totals['reparadas']:10} "
          f"{totals['falso_ok']:9} {totals['sin_mrz']:8}")

    if silent_failures:
        print(f"\nLecturas que los digitos de control dieron por buenas estando "
              f"mal ({len(silent_failures)}):")
        for apellido, condicion, real, leido in silent_failures[:12]:
            diff = "".join(
                "^" if a != b else " " for a, b in zip(real, leido)
            )
            print(f"  {apellido} / {condicion}")
            print(f"    real  {real}")
            print(f"    leido {leido}")
            print(f"          {diff}")

    print(
        "\nTodas las cedulas son sinteticas y estan rectificadas. Ninguna foto "
        "real ha pasado por aqui, asi que estos numeros son un techo, no una "
        "prevision."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
