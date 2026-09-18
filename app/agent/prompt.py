"""Construccion del prompt que ve el agente.

Las senales se listan con su identificador exacto, porque ese identificador
es el que el agente tiene que devolver en sus fundamentos para que las
citas se puedan verificar despues (ver docs/adr/0002).

Las senales que no se pudieron calcular tambien se listan, con el motivo.
Ocultarlas llevaria al agente a decidir sin saber que le falta algo, que es
justo el caso en el que deberia pedir un reenvio en vez de opinar.

EL MENU DE DECISIONES SE REESCRIBIO Y ESTE ES EL MOTIVO
-------------------------------------------------------

La primera medicion sobre calibracion (12 casos, gemini-3.1-flash-lite) dio
8 aciertos frente a los 9 de una linea base de reglas fijas.  Tres de los
cuatro fallos eran el mismo comportamiento: escalar a un humano donde tocaba
comprometerse.

No era timidez del modelo.  La version anterior definia `escalate_to_human`
como "la evidencia es **contradictoria** o esta en zona gris", y los dos
fraudes que fallo son literalmente contradicciones entre el anverso y la
MRZ.  El agente estaba obedeciendo la instruccion al pie de la letra.  El
fallo era del prompt, no de quien lo leyo.

La version nueva separa dos cosas que aquella frase mezclaba: una
contradiccion entre las dos copias del mismo dato es **evidencia**, no
duda; la zona gris es cuando caben dos lecturas y nada permite elegir.  Y
anade el caso que no tenia casilla: un documento impecable de alguien cuya
solicitud no le corresponde resolver a este sistema.

EL PESO DE UN FUNDAMENTO ES RESPECTO A APROBAR, Y HUBO QUE DECIRLO
------------------------------------------------------------------

La primera tanda con el menu reescrito dejo dos casos en
`contract_violation`, y los dos por lo mismo.  En `ambiguo-menor-de-edad`
el modelo razono bien -- "el documento es autentico y los datos son
coherentes, pero el titular es menor de edad, lo cual requiere una revision
humana" -- y fundamento su escalado con `document.age_years = 16` marcado
como `in_favor`.  El validador de `AgentDecision` exige que una decision
distinta de aprobar tenga al menos un fundamento en contra o no
concluyente, asi que la respuesta se tiro entera.

El agente acerto el caso y el contrato se lo tumbo.  Y el fallo era del
prompt: aqui se explicaba que citar, que valor poner y que se verifica,
pero **nunca que significaban los pesos**.  El modelo eligio el unico
sentido razonable sin mas informacion -- "tener 16 anos es un hecho normal
de un documento valido, luego in_favor" -- que es cierto respecto al
documento y falso respecto a la decision.

Esto aparecio al ampliar el escalado a la elegibilidad: mientras escalar
solo cubria evidencia contradictoria, siempre habia algo `against` a mano y
la ambiguedad no se notaba.

PREDICCION, ESCRITA ANTES DE MEDIR
----------------------------------

Con este prompt, sobre los mismos 12 casos de calibracion:

- `fraude-fecha-nacimiento-retocada` y `fraude-mrz-retocada-expiracion`
  deberian pasar de escalar a **reject**;
- `captura-dedo-sobre-la-fecha`, de escalar a **request_resubmission**;
- `ambiguo-menor-de-edad`, de aprobar a **escalate_to_human**;
- los 8 que ya acertaba deberian seguir acertando, y la fidelidad de las
  citas seguir en 12/12.

Si aciertan menos de 3 de esos 4, la explicacion de arriba estaba
equivocada y hay que buscar otra en vez de seguir retocando frases.  Si
alguno de los 8 que funcionaban se rompe, el arreglo cuesta mas de lo que
da.

El numero que salga de esa tanda **no sera una medida**: el prompt se
escribio mirando estos mismos casos.  La medida es el reservado, una vez.
"""

from __future__ import annotations

from app.domain.signals import UNAVAILABLE_MARKER, SignalSet

INSTRUCTIONS = """\
Eres el analista de verificacion de identidad (KYC) de una entidad
financiera. Recibes las senales medidas sobre una solicitud y decides.

Decisiones posibles:

- approve: la evidencia sostiene que la persona es quien dice ser y nada
  impide seguir adelante sin intervencion humana.
- reject: la evidencia indica suplantacion o documento no valido.
- escalate_to_human: con lo medido caben dos lecturas y nada de lo
  disponible permite elegir entre ellas, o el documento esta en regla pero
  hay algo que no te toca resolver a ti.
- request_resubmission: falta una medicion que una foto mejor si daria
  (foto borrosa, campo fuera del recorte, reflejo que tapa un dato). No es
  lo mismo que rechazar: rechazar acusa a la persona, pedir reenvio pide
  una foto mejor.

Como elegir entre ellas:

- Una contradiccion NO es motivo para escalar. Si el mismo dato viaja dos
  veces en el documento -- impreso en el anverso y dentro de la MRZ -- y
  las dos copias no coinciden sobre una imagen legible, eso ES la
  evidencia de manipulacion, no una duda sobre ella. Decide.
- No escales "para descartar un error de lectura" cuando las senales de
  calidad y de confianza ya dicen que la lectura es buena. Descartarlo es
  precisamente para lo que estan esas senales; si te fias de ellas para
  aprobar, fiate tambien para rechazar.
- Antes de escalar, preguntate que haria el analista humano que tu no
  puedes hacer. Si la respuesta es "mirar los mismos numeros que ya tienes
  delante", no escales. Si es "pedir otra foto", pide el reenvio tu.
  Escalar tiene un coste: alguien deja de atender otra cosa.
- Escala cuando una senal describa una condicion que este sistema no esta
  en posicion de resolver, aunque el documento sea impecable y la persona
  sea quien dice ser. Verificar un documento y decidir si esa solicitud
  puede seguir su curso no son la misma pregunta. Lee la descripcion de
  cada senal: varias dicen por que importan mas alla de la identidad.

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
6. El `weight` de cada fundamento se mide **respecto a aprobar**, no
   respecto a si el documento esta bien. `in_favor` significa "esto apoya
   aprobar", `against` significa "esto se opone a aprobar" e
   `inconclusive` significa "esto no deja decidir".

   El caso que confunde: una senal puede ser un dato perfectamente normal
   de un documento perfectamente valido y aun asi oponerse a aprobar. Si
   escalas o rechazas, al menos uno de tus fundamentos tiene que ser
   `against` o `inconclusive`: es el que explica por que no apruebas. Una
   decision distinta de aprobar cuyos fundamentos sean todos `in_favor` se
   rechaza por incoherente y la solicitud acaba en revision humana sin que
   tu razonamiento llegue a nadie.
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
