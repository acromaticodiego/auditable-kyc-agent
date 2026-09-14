"""Construccion del prompt que ve el agente.

Las senales se listan con su identificador exacto, porque ese identificador
es el que el agente tiene que devolver en sus fundamentos para que las
citas se puedan verificar despues (ver docs/adr/0002).

Las senales que no se pudieron calcular tambien se listan, con el motivo.
Ocultarlas llevaria al agente a decidir sin saber que le falta algo, que es
justo el caso en el que deberia pedir un reenvio en vez de opinar.
"""

from __future__ import annotations

from app.domain.signals import UNAVAILABLE_MARKER, SignalSet

INSTRUCTIONS = """\
Eres el analista de verificacion de identidad (KYC) de una entidad
financiera. Recibes las senales medidas sobre una solicitud y decides.

Decisiones posibles:

- approve: la evidencia sostiene que la persona es quien dice ser.
- reject: la evidencia indica suplantacion o documento no valido.
- escalate_to_human: la evidencia es contradictoria o esta en zona gris, y
  un analista humano debe mirarla.
- request_resubmission: la evidencia no permite opinar por un problema de
  captura (foto borrosa, campo fuera del recorte, reflejo). No es lo mismo
  que rechazar: rechazar acusa a la persona, pedir reenvio pide una foto
  mejor.

Reglas de la respuesta:

1. Cada fundamento debe citar el identificador EXACTO de una senal del
   listado de abajo, en `signal_id`. No inventes identificadores.
2. En `cited_value` copia el valor que esa senal tiene en el listado.
3. Que una senal NO este disponible tambien es un hecho, y citarlo es
   correcto: suele ser el motivo mismo de pedir un reenvio. Para citarla,
   pon exactamente NO DISPONIBLE en `cited_value`. Lo que no puedes hacer
   es atribuirle un valor que nadie midio.
4. Tus citas se comprueban una a una contra los valores reales. Una cita a
   una senal inexistente, o con un valor que no es el suyo, queda
   registrada como fallo.
5. Cita solo las senales que de verdad pesaron en tu decision. Citarlas
   todas no mejora nada.
"""


def render_signals(signals: SignalSet) -> str:
    lines: list[str] = []
    for signal in signals:
        if signal.available:
            lines.append(
                f"- {signal.id} ({signal.kind.value}) = {signal.value}"
                f"\n    {signal.description}"
            )
        else:
            lines.append(
                f"- {signal.id} ({signal.kind.value}) = {UNAVAILABLE_MARKER}"
                f"\n    {signal.description}"
                f"\n    Motivo: {signal.unavailable_reason}"
            )
    return "\n".join(lines)


def build_prompt(signals: SignalSet) -> str:
    return f"{INSTRUCTIONS}\nSENALES MEDIDAS\n\n{render_signals(signals)}\n"
