# ADR-0001: El agente recibe las señales baratas precalculadas y solo pide las caras

- **Fecha:** 2026-09-14
- **Estado:** aceptada

## Contexto

El agente decide sobre cada verificación con un modelo de lenguaje servido
por la API de Gemini en **plan gratuito**, cuyo cupo medido en este entorno
es de **20 peticiones al día por modelo**. Cambiar el nombre del modelo
concede otras 20, pero eso no resuelve el problema de fondo.

El diseño natural de un agente con uso de herramientas es darle un catálogo
y dejar que pida lo que necesite: leer el documento, comparar las caras,
medir el desenfoque, consultar el formato del número. Cada petición del
agente es una llamada a la API, y cada respuesta a una herramienta obliga a
otra llamada más para continuar. En la práctica son **4 o 5 peticiones por
caso**.

El proyecto necesita un conjunto de evaluación de unas 40 verificaciones y
necesita poder repetirlo cada vez que cambie el prompt. Con el diseño
natural, una sola tanda cuesta entre 160 y 200 peticiones: **ocho días de
cupo para una única medición** que habrá que repetir decenas de veces. El
diseño natural no es viable aquí.

## Decisión

Las señales se parten en dos grupos según su coste real de cómputo, no
según lo interesantes que sean:

**Señales baratas y deterministas** — OCR del documento y su confianza,
similitud facial entre documento y selfie, varianza del laplaciano como
medida de desenfoque, validación del formato del número de cédula,
coherencia de la fecha de vencimiento. Todas se calculan **siempre en
local, antes de llamar al modelo**, y viajan completas en el prompt
inicial. No cuestan peticiones y no dependen de que el agente se acuerde
de pedirlas.

**Señales caras o condicionales** — reprocesar el OCR con otro
preprocesado sobre una región concreta, ampliar un recorte del documento,
consultar una lista de riesgo. Estas sí son herramientas que el agente
invoca **solo cuando la evidencia inicial no le basta**.

El bucle del agente queda limitado a `agent_max_turns` vueltas (3 por
defecto). Además, toda respuesta del modelo se cachea en disco con clave
`hash(prompt exacto + nombre del modelo)`, de modo que repetir una tanda
sin haber tocado el prompt **cuesta cero peticiones**.

## Alternativas descartadas

**Dejar que el agente pida todas las señales.** Es el diseño más
elegante y el más agéntico sobre el papel. Descartado por el cupo: haría
imposible medir nada, y un proyecto cuya tesis es que dice la verdad sobre
sí mismo con datos no puede permitirse no poder medirse.

**Darle absolutamente todas las señales de una vez, sin herramientas.**
Resuelve el cupo del todo (una petición por caso) pero entonces esto no es
un agente, es un clasificador que escribe prosa. Se pierde justo lo que el
proyecto quiere demostrar: que ante evidencia ambigua el sistema puede
decidir que necesita más información antes de opinar.

**Usar Gemini como OCR multimodal.** Tentador porque su lectura de
documentos es mejor que la de Tesseract. Descartado: convertiría la
extracción, que se ejecuta en todos los casos, en un consumidor del cupo,
y dejaría sin peticiones a la parte que de verdad diferencia al proyecto.
El OCR local además aporta una señal que un modelo multimodal no da
gratis: una **confianza por campo** sobre la que el agente puede razonar.

**Pagar la API.** Fuera del alcance por decisión de presupuesto. Vale la
pena señalar que la restricción resultó productiva: obligó a un diseño
con caché y presupuesto explícito de peticiones que es exactamente lo que
haría falta en producción por motivos de coste y latencia, no de cupo.

## Consecuencias

- El número medio de peticiones por caso pasa a ser **una métrica del
  sistema**, que hay que medir y reportar junto a la tasa de acierto.
- La caché convierte el desarrollo del prompt en algo iterable, pero
  introduce el riesgo de medir contra respuestas viejas. La herramienta de
  evaluación debe registrar qué proporción de la tanda salió de caché.
- Precalcular todas las señales baratas cuesta tiempo de CPU en cada
  verificación aunque el agente no llegue a usarlas. Es un intercambio
  aceptado: sobra CPU local y falta cupo de API.
- La rotación de modelos queda permitida para desarrollar y **prohibida
  dentro de una misma tanda de evaluación**: mezclar dos modelos y
  presentar un solo porcentaje sería medir dos sistemas distintos.
