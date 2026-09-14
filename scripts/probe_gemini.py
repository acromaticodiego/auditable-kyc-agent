"""Sonda: ¿respeta Gemini el contrato de decision?

Es la pregunta mas incierta del proyecto y la que mas cambiaria el diseno
si la respuesta fuera que no, asi que se responde antes de invertir nada en
OCR ni en reconocimiento facial.  Gasta una peticion del cupo por caso, y
las respuestas quedan cacheadas: repetir la sonda sin tocar el prompt no
gasta nada.

    docker compose exec api python scripts/probe_gemini.py
"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from app.agent.cache import ResponseCache
from app.agent.gemini import (
    GeminiClient,
    GeminiError,
    QuotaExhausted,
    parse_json_response,
)
from app.agent.prompt import build_prompt
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.config import settings
from app.domain.citation_audit import audit_citations
from app.domain.decision import AgentDecision
from app.domain.signals import Signal, SignalKind, SignalSet

# Tres casos construidos a mano, uno por cada tipo de respuesta que el
# sistema deberia saber dar.  No son datos reales ni pretenden medir
# acierto: solo sirven para ver si el modelo devuelve la estructura pedida
# y si cita las senales que se le dieron.
CASES: dict[str, SignalSet] = {
    "claro y favorable": SignalSet(
        [
            Signal("facial.similarity", SignalKind.SCORE,
                   "Similitud entre la cara del documento y la de la selfie "
                   "(0 a 1).", value=0.89),
            Signal("ocr.name_confidence", SignalKind.CONFIDENCE,
                   "Confianza del OCR al leer el nombre.", value=0.97),
            Signal("ocr.full_name", SignalKind.TEXT,
                   "Nombre completo leido del documento.",
                   value="MARIA FERNANDA GOMEZ"),
            Signal("document.number_format_valid", SignalKind.FLAG,
                   "El numero de cedula cumple el formato esperado.",
                   value=True),
            Signal("document.expired", SignalKind.FLAG,
                   "El documento esta vencido a dia de hoy.", value=False),
            Signal("quality.blur_regions", SignalKind.COUNT,
                   "Regiones del documento por debajo del umbral de nitidez.",
                   value=0),
        ]
    ),
    "ambiguo": SignalSet(
        [
            Signal("facial.similarity", SignalKind.SCORE,
                   "Similitud entre la cara del documento y la de la selfie "
                   "(0 a 1). Por debajo de 0.70 no basta para aprobar sola.",
                   value=0.61),
            Signal("ocr.name_confidence", SignalKind.CONFIDENCE,
                   "Confianza del OCR al leer el nombre.", value=0.94),
            Signal("ocr.full_name", SignalKind.TEXT,
                   "Nombre completo leido del documento.",
                   value="JUAN DIEGO OSSA"),
            Signal("document.number_format_valid", SignalKind.FLAG,
                   "El numero de cedula cumple el formato esperado.",
                   value=True),
            Signal("ocr.expiry_date", SignalKind.TEXT,
                   "Fecha de vencimiento del documento.",
                   unavailable_reason="el campo quedo fuera del recorte"),
            Signal("quality.blur_regions", SignalKind.COUNT,
                   "Regiones del documento por debajo del umbral de nitidez.",
                   value=2),
        ]
    ),
    "captura mala": SignalSet(
        [
            Signal("facial.similarity", SignalKind.SCORE,
                   "Similitud entre la cara del documento y la de la selfie.",
                   unavailable_reason="no se detecto ninguna cara en el documento"),
            Signal("ocr.name_confidence", SignalKind.CONFIDENCE,
                   "Confianza del OCR al leer el nombre.", value=0.31),
            Signal("ocr.full_name", SignalKind.TEXT,
                   "Nombre completo leido del documento.",
                   value="JJAN D1EG0 0SSA"),
            Signal("document.number_format_valid", SignalKind.FLAG,
                   "El numero de cedula cumple el formato esperado.",
                   value=False),
            Signal("quality.blur_regions", SignalKind.COUNT,
                   "Regiones del documento por debajo del umbral de nitidez.",
                   value=7),
        ]
    ),
}


def probe(name: str, signals: SignalSet, client: GeminiClient) -> bool:
    print(f"\n{'=' * 70}\nCASO: {name}\n{'=' * 70}")

    response = client.generate_json(build_prompt(signals), DECISION_RESPONSE_SCHEMA)
    print(f"modelo: {response.model}   de cache: {response.from_cache}")

    payload = parse_json_response(response.text)

    try:
        decision = AgentDecision.model_validate(payload)
    except ValidationError as error:
        print("LA RESPUESTA NO CUMPLE EL CONTRATO:")
        print(error)
        return False

    print(f"decision: {decision.decision.value}")
    print(f"resumen:  {decision.summary}")

    report = audit_citations(decision, signals)
    print(f"citas fieles: {report.faithful}  ({len(report.invalid)} invalidas "
          f"de {len(report.results)})")

    for grounding, result in zip(decision.groundings, report.results, strict=True):
        mark = "ok " if result.valid else "!! "
        print(f"  {mark}[{grounding.weight.value:13}] {result.signal_id} "
              f"= {result.cited_value!r} (real: {result.actual_value!r}) "
              f"-> {result.status.value}")
        print(f"      {grounding.text}")

    return True


def main() -> int:
    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        cache=ResponseCache(".llm_cache"),
    )

    contract_ok = 0
    try:
        for name, signals in CASES.items():
            if probe(name, signals, client):
                contract_ok += 1
    except QuotaExhausted as error:
        print(f"\nCUPO AGOTADO, se para la tanda:\n{error}")
        return 2
    except GeminiError as error:
        print(f"\nFALLO DE LA API:\n{error}")
        return 1

    print(f"\n{'=' * 70}")
    print(f"contrato respetado en {contract_ok} de {len(CASES)} casos "
          f"(muestra de {len(CASES)}: insuficiente para afirmar fiabilidad)")
    return 0 if contract_ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
