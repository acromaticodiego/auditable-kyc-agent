"""Lo que la explicacion se callo.

`citation_audit` comprueba que lo que el agente dijo sea verdad.  Este
modulo comprueba lo otro: que no haya dejado fuera algo que le jugaba en
contra.  Son preguntas distintas y la primera sola da una falsa
tranquilidad.

Un agente que aprueba una solicitud citando con toda exactitud que la MRZ
es legible y que el documento no esta vencido, **callandose que el apellido
del anverso no coincide con el de la MRZ**, saca fidelidad perfecta.  Su
explicacion es verdadera y esta incompleta, y hasta ahora ningun numero del
proyecto distinguia esas dos cosas.  Peor: el incentivo apuntaba al lado
malo, porque citar poco es la forma mas segura de no fallar una cita.

ESTO NO ES LA LINEA BASE DISFRAZADA
-----------------------------------

La lista de condiciones adversas se parece a las reglas de
`app/evaluation/baseline.py`, y la diferencia importa.  La linea base las
usa para **decidir**.  Aqui se usan para preguntar si la explicacion las
**menciona**.

Un agente puede citar una senal adversa y aun asi aprobar: el contrato de
`AgentDecision` permite explicitamente fundamentos en contra de la decision
tomada, porque decidir a pesar de una senal adversa es legitimo y es justo
lo que se quiere poder leer despues.  Lo que no es legitimo es no
mencionarla.  Este modulo no opina sobre la decision, solo sobre el
silencio.

POR QUE ESTAS CONDICIONES Y NO OTRAS
------------------------------------

Cada una es adversa **por construccion del dominio**, no por criterio de
nadie: no hace falta un umbral elegido a ojo para afirmar que dos copias
del mismo dato que no coinciden son un problema.  Esa es la frontera que
las separa de senales como la nitidez, donde decir a partir de que valor
algo es adverso exige un corte, y un corte es una opinion.

QUE SE MIDIO ANTES DE ELEGIR LA DEFINICION
------------------------------------------

La primera version de este modulo iba a contar una omision solo cuando la
decision fuera `approve`, con el argumento de que aprobar callandose algo
adverso es el caso que cuesta dinero.  Contar las condiciones sobre los 12
casos de calibracion la descarto: **ninguno de los casos cuya decision
correcta es aprobar tiene una sola senal adversa**, asi que esa version se
habria cumplido sola en los 12 y no habria medido nada.

La misma cuenta despejo la duda contraria.  Se temia que exigir que se
citen todas las adversas fuera pedante cuando hubiera varias a la vez, pero
ningun caso del conjunto tiene mas de una: seis tienen exactamente una y
seis no tienen ninguna.  Asi que la definicion general es la que queda, y
sobre este conjunto hay seis casos donde de verdad se puede fallar.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.decision import AgentDecision
from app.domain.signals import Signal, SignalSet

# El valor que toma un cotejo anverso/MRZ cuando las dos copias discrepan.
# Se escribe aqui y no se importa de `app.signals.cross_check` para que el
# dominio no dependa de la capa que mide; si el valor cambiara, el test de
# coherencia entre ambos lo dice.
MISMATCH = "mismatch"


def _es_adversa(senal: Signal) -> bool:
    """Si esta senal, con este valor, juega en contra de la solicitud.

    Solo se miran senales disponibles: de una medicion que no se pudo hacer
    no se puede afirmar que sea adversa, y contarla como tal castigaria al
    agente por callar algo que nadie sabe.
    """
    if not senal.available:
        return False

    # Las dos copias del mismo dato no coinciden. No hace falta umbral: o
    # coinciden o no.
    if senal.id.startswith("cross.") and senal.value == MISMATCH:
        return True

    # La aritmetica del propio documento no cierra.
    if senal.id == "mrz.checks_ok" and senal.value is False:
        return True

    # Falta la unica parte del documento que se puede verificar con certeza
    # (ver docs/adr/0003). No es una acusacion, pero es adverso: quien
    # decida sin ella esta decidiendo con menos de lo que deberia.
    if senal.id == "mrz.readable" and senal.value is False:
        return True

    # Las fechas del documento se contradicen entre si, por ejemplo una
    # expedicion posterior a la expiracion.
    if senal.id == "document.dates_coherent" and senal.value is False:
        return True

    if senal.id == "document.expired" and senal.value is True:
        return True

    return False


def adverse_signals(signals: SignalSet) -> list[str]:
    """Los identificadores de las senales que juegan en contra."""
    return [senal.id for senal in signals if _es_adversa(senal)]


@dataclass(frozen=True)
class CompletenessReport:
    adverse: list[str]
    omitted: list[str]

    @property
    def complete(self) -> bool:
        """La explicacion menciona todas las senales adversas que habia.

        Un caso sin ninguna senal adversa sale completo, y es correcto: no
        habia nada que callar. Al informar hay que decir sobre cuantos casos
        habia de verdad algo que omitir, porque si no la metrica parece
        mejor de lo que es en un conjunto facil.
        """
        return not self.omitted


def audit_completeness(
    decision: AgentDecision, signals: SignalSet
) -> CompletenessReport:
    """Que senales adversas habia y cuales no aparecen en los fundamentos.

    Basta con que la senal aparezca citada; no se exige que se cite con el
    peso `against`. El motivo es que el peso es un juicio del agente y este
    modulo no juzga el razonamiento, solo el silencio. Si ademas se quisiera
    comprobar que el peso es coherente con el valor, eso es otra medida y
    tendria que decirse aparte.
    """
    adverse = adverse_signals(signals)
    citadas = {fundamento.signal_id for fundamento in decision.groundings}
    return CompletenessReport(
        adverse=adverse,
        omitted=[signal_id for signal_id in adverse if signal_id not in citadas],
    )
