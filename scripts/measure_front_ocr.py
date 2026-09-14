"""Mide el acierto del OCR del anverso, campo a campo.

A diferencia de la MRZ, el anverso **no trae nada que delate una lectura
erronea**: si el OCR confunde un apellido, dentro de la imagen no hay
forma de saberlo.  Aqui si se puede medir porque las cedulas son
sinteticas, y ese numero es el techo de lo que se puede esperar.

Lo que el sistema tendra en produccion no es esta tabla, sino la confianza
que reporta Tesseract.  Por eso se mide tambien **si la confianza sirve
para algo**: se compara la confianza media de las lecturas correctas con
la de las equivocadas.  Si fueran parecidas, la confianza no distinguiria
nada y el agente no deberia razonar sobre ella.

    docker compose exec api python scripts/measure_front_ocr.py
"""

from __future__ import annotations

import sys
from collections import defaultdict

from app.signals.ocr_front import (
    parse_nuip,
    parse_place_and_date,
    parse_spanish_date,
    read_front,
)
from app.synthetic.cedula import render_front
from app.synthetic.degradation import blur, downscale, jpeg_artifacts
from scripts.measure_mrz_ocr import CONDITIONS, IDENTITIES, build

CHECKS = {
    "nuip": lambda fields, data: parse_nuip(fields.value("nuip") or "") == data.nuip,
    "surnames": lambda fields, data: (fields.value("surnames") or "").upper()
    == data.surnames,
    "given_names": lambda fields, data: (fields.value("given_names") or "").upper()
    == data.given_names,
    "birth_date": lambda fields, data: parse_spanish_date(
        fields.value("birth_date") or ""
    )
    == data.birth_date,
    "expiry_date": lambda fields, data: parse_spanish_date(
        fields.value("expiry_date") or ""
    )
    == data.expiry_date,
    "issue_date": lambda fields, data: parse_place_and_date(
        fields.value("issue") or ""
    )[0]
    == data.issue_date,
    "sex": lambda fields, data: (fields.value("sex") or "").upper() == data.sex,
    "nationality": lambda fields, data: (fields.value("nationality") or "").upper()
    == data.nationality,
}


def main() -> int:
    hits: dict[str, int] = defaultdict(int)
    found: dict[str, int] = defaultdict(int)
    per_condition: dict[str, dict[str, int]] = {
        name: defaultdict(int) for name in CONDITIONS
    }
    confidences: dict[bool, list[float]] = {True: [], False: []}
    total = 0

    for identity in IDENTITIES:
        data = build(identity)
        front = render_front(data)

        for condition, degrade in CONDITIONS.items():
            fields = read_front(degrade(front))
            total += 1

            for field, check in CHECKS.items():
                source = "issue" if field == "issue_date" else field
                if fields.get(source) is not None:
                    found[field] += 1
                correct = bool(check(fields, data))
                if correct:
                    hits[field] += 1
                    per_condition[condition][field] += 1

                confidence = fields.confidence(source)
                if confidence is not None:
                    confidences[correct].append(confidence)

    print(f"{len(IDENTITIES)} identidades x {len(CONDITIONS)} condiciones = "
          f"{total} lecturas del anverso\n")

    print(f"{'campo':14} {'localizado':>11} {'correcto':>10}")
    for field in CHECKS:
        print(f"{field:14} {found[field]:7}/{total:<3} {hits[field]:6}/{total:<3}")

    print(f"\n{'condicion':16} " + " ".join(f"{f[:9]:>10}" for f in CHECKS))
    for condition in CONDITIONS:
        row = " ".join(
            f"{per_condition[condition][f]:>7}/{len(IDENTITIES):<2}" for f in CHECKS
        )
        print(f"{condition:16} {row}")

    print("\n¿Sirve de algo la confianza que reporta Tesseract?")
    for correct in (True, False):
        values = confidences[correct]
        etiqueta = "lecturas correctas " if correct else "lecturas equivocadas"
        if values:
            print(f"  {etiqueta}: n={len(values):4}  "
                  f"confianza media={sum(values) / len(values):.3f}  "
                  f"minima={min(values):.2f}")
        else:
            print(f"  {etiqueta}: ninguna")

    print(
        "\nCedulas sinteticas y rectificadas. El anverso no tiene forma de "
        "delatar una lectura erronea, asi que en produccion este acierto no "
        "se puede observar: solo la confianza."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
