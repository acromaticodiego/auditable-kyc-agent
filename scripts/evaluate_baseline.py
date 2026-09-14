"""Evalua la linea base de reglas fijas sobre las dos mitades.

El protocolo importa mas que el numero:

- Sobre **calibracion** el resultado sale alto por construccion, porque los
  umbrales se eligieron mirando esos mismos nueve casos. Se imprime para
  poder compararlo con el otro y ver la diferencia, no como medida.
- Sobre el **reservado** es donde se mide de verdad. Ejecutar esto lo quema:
  a partir de aqui, ajustar los umbrales mirando este resultado convertiria
  la siguiente medicion en otro numero elegido sobre sus propios datos.

Si el reservado sale peor que la calibracion, eso **es** el resultado y se
publica tal cual.

    docker compose exec api python scripts/evaluate_baseline.py
"""

from __future__ import annotations

import sys
from collections import Counter

from app.domain.citation_audit import audit_citations
from app.evaluation.baseline import decide
from app.evaluation.catalog import TODAY, load_cases
from app.evaluation.split import CALIBRATION, HOLDOUT
from app.signals.pipeline import build_signals


def run(split: str, *, final: bool) -> dict:
    casos = sorted(load_cases(split, final_measurement=final), key=lambda c: c.id)
    aciertos = 0
    fieles = 0
    fallos: list[tuple[str, str, str, str]] = []
    por_decision: Counter[str] = Counter()

    print(f"\n{'=' * 78}")
    if split == CALIBRATION:
        print("CALIBRACION — los umbrales se eligieron aqui. NO es una medida.")
    else:
        print("RESERVADO — medicion final. Sale lo que salga.")
    print("=" * 78)
    print(f"{'caso':40} {'esperado':22} {'linea base':22}")

    for caso in casos:
        signals = build_signals(*caso.build(), today=TODAY)
        decision = audit = decide(signals)
        report = audit_citations(audit, signals)

        correcto = decision.decision is caso.expected_decision
        aciertos += correcto
        fieles += report.faithful
        por_decision[caso.expected_decision.value] += correcto

        marca = "ok" if correcto else "XX"
        print(
            f"{caso.id:40} {caso.expected_decision.value:22} "
            f"{decision.decision.value:22} {marca}"
        )
        if not correcto:
            fallos.append(
                (caso.id, caso.expected_decision.value, decision.decision.value,
                 caso.reason)
            )

    total = len(casos)
    print(f"\naciertos: {aciertos}/{total}    explicaciones fieles: {fieles}/{total}")

    if fallos:
        print("\nDonde falla y por que deberia haber decidido otra cosa:")
        for caso_id, esperado, obtenido, motivo in fallos:
            print(f"\n  {caso_id}")
            print(f"    esperado {esperado}, decidio {obtenido}")
            print(f"    {motivo[:300]}")

    return {"total": total, "aciertos": aciertos, "fieles": fieles, "fallos": fallos}


def main() -> int:
    calibracion = run(CALIBRATION, final=False)
    reservado = run(HOLDOUT, final=True)

    print(f"\n{'=' * 78}")
    print("RESUMEN")
    print("=" * 78)
    for nombre, datos in (("calibracion", calibracion), ("reservado", reservado)):
        print(
            f"  {nombre:12} {datos['aciertos']}/{datos['total']} aciertos, "
            f"{datos['fieles']}/{datos['total']} explicaciones fieles"
        )
    print(
        "\n  El numero de la izquierda esta inflado por construccion: los cortes\n"
        "  se eligieron sobre esos mismos casos. El de la derecha es la medida."
    )
    print(
        "\n  Muestra: 18 casos sinteticos derivados de una sola identidad. Sirve\n"
        "  para comparar la linea base con el agente sobre el mismo material, no\n"
        "  para prever el comportamiento con documentos reales."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
