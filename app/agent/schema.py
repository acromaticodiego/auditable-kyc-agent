"""Esquema de respuesta estructurada que se le impone al modelo.

Se escribe a mano en vez de derivarlo de `AgentDecision.model_json_schema()`
porque Gemini no acepta el esquema que produce Pydantic: las listas de
submodelos salen como referencias `$defs`/`$ref` y la API las rechaza.
Derivarlo y limpiarlo despues era mas fragil que escribirlo explicito.

Los valores de los enumerados si se toman de las clases del dominio, que
es donde se pueden desincronizar de verdad sin que nadie lo note.
"""

from __future__ import annotations

from app.domain.decision import DecisionKind, Weight

# `cited_value` viaja como cadena a proposito.
#
# El valor citado puede ser un score, un entero, un texto o un booleano, y
# el esquema de Gemini no admite una union de tipos primitivos en un mismo
# campo.  Pedirlo siempre como cadena evita que el modelo tenga que elegir
# tipo, y la comparacion con el valor real se hace en el auditor, que ya
# sabe interpretar "0.61" o "true" segun el tipo de la senal citada.
DECISION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [kind.value for kind in DecisionKind],
        },
        "summary": {
            "type": "string",
            "description": "Resumen en una o dos frases de la decision.",
        },
        "groundings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": (
                            "Identificador exacto de una senal del listado. "
                            "No inventar identificadores."
                        ),
                    },
                    "cited_value": {
                        "type": "string",
                        "description": (
                            "El valor que esa senal tiene, copiado del listado."
                        ),
                    },
                    "weight": {
                        "type": "string",
                        "enum": [weight.value for weight in Weight],
                    },
                    "text": {
                        "type": "string",
                        "description": "Por que esa senal pesa en la decision.",
                    },
                },
                "required": ["signal_id", "cited_value", "weight", "text"],
                "propertyOrdering": ["signal_id", "cited_value", "weight", "text"],
            },
        },
    },
    "required": ["decision", "summary", "groundings"],
    "propertyOrdering": ["decision", "summary", "groundings"],
}
