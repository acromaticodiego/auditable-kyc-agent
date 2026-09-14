# ADR-0004: Cuando el agente no contesta, el sistema escala a un humano

- **Fecha:** 2026-09-14
- **Estado:** aceptada

## Contexto

El bucle del agente (`app/agent/runner.py`) puede acabar de cinco maneras, y
solo una de ellas produce una decisión:

1. el modelo responde y su respuesta cumple el contrato `AgentDecision`;
2. responde un JSON válido que **incumple** el contrato: cita sin valor, o
   aprueba fundamentándose únicamente en señales en contra;
3. responde algo que ni siquiera es JSON, pese al esquema impuesto;
4. no responde: corte de red, corte por tiempo, filtro de seguridad, un 5xx
   que no cedió al reintento;
5. se acabó el cupo diario.

Los cuatro finales sin decisión no son raros. Durante las sondas previas
aparecieron 503 de sobrecarga en tres de cada cinco modelos probados, y el
cupo gratuito se agota con doce peticiones fallidas. Un sistema de
verificación de identidad que solo esté diseñado para el camino feliz
decide mal justo cuando más incómodo es equivocarse.

La pregunta, entonces, no es si estos finales van a ocurrir, sino **qué le
pasa a la solicitud de una persona real cuando ocurren**.

## Decisión

Los cuatro finales sin decisión llevan a `escalate_to_human`. Ni aprobar ni
rechazar, en ningún caso y por ningún motivo técnico.

**Aprobar por defecto es el fallo caro.** Un sistema que aprueba cuando su
proveedor falla le regala a un atacante un procedimiento: provocar el fallo
y pasar. No hace falta que el atacante entienda el modelo; le basta con
esperar a que el proveedor tenga un mal día.

**Rechazar por defecto es el fallo injusto.** Rechazar en un KYC no es
"volver a intentarlo": es dejar registrado que esa persona no acreditó ser
quien dice ser. Hacerlo porque se cayó una API significa acusar a alguien
de suplantación por un motivo que no tiene nada que ver con su documento.

**Pedir reenvío tampoco sirve**, aunque es tentador porque parece inocuo.
Le diría a la persona que su foto tiene un problema cuando el problema es
nuestro, y la pondría a repetir capturas que no van a arreglar nada. Además
haría invisible el fallo: una racha de caídas de la API se leería en las
métricas como una racha de fotos malas.

Escalar es lo único que no miente sobre lo que el sistema sabe en ese
momento, que es nada.

La consecuencia práctica es que la cola de revisión humana absorbe las
caídas del proveedor. Eso es un coste real y conviene decirlo en vez de
disimularlo: si la API se cae una mañana entera, esa mañana entera de
solicitudes acaba en manos de analistas. Es el comportamiento correcto y es
caro, y las dos cosas son ciertas a la vez.

## Lo que se midió y lo que no

El comportamiento está cubierto por un test que **barre todos los finales
del enumerado**, no caso por caso, de modo que un final nuevo queda cubierto
sin que nadie se acuerde de ampliarlo. Se comprobó rompiéndolo a propósito:
al cambiar el destino del fallback a `approve`, fallan ocho tests; al
fusionar el final de cupo agotado con el de API caída, falla el que
distingue los dos.

Lo que **no** se ha medido todavía es con qué frecuencia ocurre cada final
contra el modelo real, porque para eso hace falta una tanda completa sobre
calibración y el cupo del día ya estaba gastado. Ese número es parte del
resultado: un agente que incumple el contrato en uno de cada tres casos
manda un tercio de las solicitudes a revisión humana, y eso lo descalifica
por mucho que acierte en el resto.

## Alternativas descartadas

**Reintentar devolviéndole al modelo su error de validación.** Es lo
habitual y en otro proyecto sería lo correcto. Aquí se descartó por dos
motivos. El menor es el cupo: repetir el mismo prompt devuelve la respuesta
mala desde la caché, así que el reintento útil exige un prompt nuevo, que es
una petición más sobre un tope de veinte diarias. El mayor es que reparar en
silencio **esconde con qué frecuencia el modelo rompe el contrato**, y esa
frecuencia es justo uno de los números que este proyecto existe para
publicar. Si más adelante el reintento resulta necesario, tendrá que contar
aparte los casos que necesitaron corrección.

**Degradar a `escalate_to_human` toda decisión con citas falsas.** Es
defendible: si la explicación no se sostiene, la decisión que la acompaña
tampoco debería. Se descartó *por ahora* por disciplina, no por desacuerdo.
Sin haber medido cuántas citas falsas hay ni de qué tipo —inventarse una
señal no es lo mismo que redondear mal un score—, esa regla sería un corte
elegido a ojo, y este proyecto no elige cortes a ojo. Queda como pregunta
abierta hasta tener el número sobre calibración, y el test que fija el
comportamiento actual dice explícitamente que debe cambiarse el día que
haya un número que lo justifique.

**Dejar que el fallo suba como excepción y que decida quien llame.** Mueve
la decisión a cada punto de llamada y garantiza que tarde o temprano alguien
la resuelva con un `try/except` que aprueba. La política vive en un sitio y
está escrita.
