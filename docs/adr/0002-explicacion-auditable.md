# ADR-0002: La explicación del agente cita señales con su valor, y las citas se verifican

- **Fecha:** 2026-09-14
- **Estado:** aceptada

## Contexto

Un modelo de lenguaje explica sus decisiones con enorme soltura. Ese es el
problema. La explicación de un rechazo de KYC es un documento con
consecuencias: puede acabar ante un regulador o ante la persona a la que
se le negó una cuenta. Un párrafo bien escrito que atribuya a la
verificación un dato que nunca se midió es **peor que no dar explicación
alguna**, porque parece auditado y no lo está.

La pregunta que hace cualquiera en banca ante un sistema así es siempre la
misma: *¿y si el modelo se inventa la razón?* Sin una respuesta medible,
el proyecto entero se apoya en la buena fe.

## Decisión

El agente devuelve una estructura, no texto libre. Cada fundamento lleva
**el identificador de una señal y el valor que el agente le atribuye**:

```json
{ "signal_id": "facial.similarity", "cited_value": 0.61,
  "weight": "against",
  "text": "La similitud facial queda en tierra de nadie." }
```

Después de cada decisión, `audit_citations` contrasta cada cita con el
valor real de la señal y clasifica el resultado en uno de cinco estados:
válida, señal inexistente, señal que no se pudo calcular, valor que no
coincide, o cita sin valor. Una decisión es **fiel** si ninguna de sus
citas es inválida.

Tres detalles del diseño no son cosméticos:

**Citar la ausencia de una señal es válido; inventarle un valor, no.** Una
señal que no se pudo calcular se le presenta al agente como `NO DISPONIBLE`
y puede citarla escribiendo ese mismo marcador. Lo que falla es atribuirle
una fecha o un número que nadie midió. Esta regla no estaba en la primera
versión y la impuso la realidad: ver más abajo.

**Citar sin valor cuenta como cita inválida.** Si dejar `cited_value` en
nulo saliera gratis, la estrategia segura para el modelo sería no citar
valores nunca, y la métrica dejaría de medir. La regla existe por el
incentivo que crea, no por el caso en sí.

**La decisión exige al menos un fundamento, y en la dirección correcta.**
Sin el mínimo, una decisión sin fundamentos tendría fidelidad perfecta por
vacuidad. Aprobar exige algún fundamento a favor; rechazar, escalar o
pedir reenvío exigen al menos uno en contra o no concluyente.

**La tolerancia numérica es 0,011 y esa cifra decide la métrica.** Los
modelos citan los scores con dos decimales y algunos truncan en vez de
redondear, lo que produce diferencias de hasta 0,0099 que no son errores.
Relajar la tolerancia infla la fidelidad hasta volverla trivial: por eso
va acompañada de un test de frontera que comprueba que 0,012 sigue
fallando.

## Alternativas descartadas

**Prosa libre y confiar.** Es lo que hace casi todo el mundo. Descartada
porque no produce ningún número, y este proyecto se juzga por números.

**Pedirle al modelo una puntuación de confianza.** Es un autoinforme: el
modelo que se inventa un dato se inventa igual de bien la confianza con
que lo afirma. No verifica nada, solo añade una cifra tranquilizadora.

**Un segundo modelo que juzgue la explicación del primero.** Popular, y
descartada por dos motivos: duplica el consumo del cupo (ver ADR-0001) y
el juez alucina como el juzgado, así que sustituye una creencia por otra
en vez de dar una comprobación dura. La verificación de citas es
determinista y cuesta cero peticiones.

**Eliminar la prosa y generar la explicación con plantillas.** Sería
perfectamente auditable y perfectamente inútil: equivale a volver al árbol
de reglas y perder lo único que aporta el agente, que es articular por qué
dos señales en conflicto se resuelven de una manera y no de otra.

**Exigir coherencia estricta entre los pesos y la decisión.** Tentador:
prohibir que se apruebe si hay algún fundamento en contra. Descartado
porque decidir *a pesar de* una señal adversa es legítimo y es justo lo
que interesa poder leer después. Solo se valida que exista al menos un
fundamento en la dirección de la decisión.

## Consecuencias

- Aparece la primera métrica del proyecto: **fidelidad de las citas**, que
  se calcula sobre cualquier decisión guardada, sin etiquetado humano y
  sin gastar cupo.
- **Esto no mide si el razonamiento es correcto.** Un agente puede citar
  todos los valores con exactitud y aun así sacar una conclusión
  disparatada. La fidelidad de las citas acota la invención de datos, no
  la calidad del juicio; para eso está la comparación contra la línea base
  de reglas fijas. Presentarla como si midiera lo segundo sería exactamente
  el tipo de número engañoso que este proyecto quiere evitar.
- **La fidelidad mide que no mienta, no que lo cuente todo.** Es el límite
  más fácil de olvidar al leer un `38/38`, y el más incómodo. El auditor
  recorre las citas que el agente *hizo* y comprueba una a una que digan la
  verdad; no tiene forma de ver las que **no** hizo. Un agente que aprueba
  una solicitud citando con toda exactitud que la MRZ es legible y que el
  documento no está vencido, **callándose que el apellido del anverso no
  coincide con el de la MRZ**, saca fidelidad perfecta. Su explicación es
  verdadera y está incompleta, y el número no distingue esas dos cosas.

  Peor aún: el incentivo apunta en la dirección equivocada. Citar poco es
  la forma más segura de no equivocarse en una cita. El mínimo de un
  fundamento en `AgentDecision` cierra el caso extremo de no citar nada,
  pero no el de citar sólo lo cómodo.

  Por eso se añadió una segunda medida, la **completitud**
  (`app/domain/completeness.py`): si existía alguna señal adversa que la
  explicación no menciona. Es calculable y determinista —un cotejo en
  `mismatch`, un documento vencido, unos dígitos de control que no cuadran
  son adversos por construcción del dominio, no por criterio de nadie— y
  **no juzga la decisión, solo el silencio**: citar una señal adversa y
  aun así aprobar es legítimo, y es justo lo que se quiere poder leer
  después.

  Su definición se eligió midiendo antes de fijarla. La primera versión
  iba a contar la omisión únicamente cuando la decisión fuera `approve`,
  con el argumento de que aprobar callándose algo adverso es lo que cuesta
  dinero; contar las condiciones sobre los 12 casos de calibración la
  descartó, porque ninguno de los casos cuya decisión correcta es aprobar
  tiene una sola señal adversa, de modo que esa versión se habría cumplido
  sola en los 12 y no habría medido nada.

  Aun con las dos medidas, una cifra de fidelidad se lee como «de lo que
  dijo, nada era falso», nunca como «la explicación está completa». Son dos
  números y hay que publicar los dos.

- Los identificadores de señal pasan a ser una interfaz pública. Renombrar
  `facial.similarity` invalida las citas de todas las decisiones ya
  guardadas.
- **La primera sonda contra la API real refutó una de las reglas.** Ante un
  caso con la fecha de vencimiento fuera del recorte, el modelo pidió
  reenvío —la decisión correcta— y lo fundamentó citando esa ausencia,
  copiando el marcador tal cual del listado. El auditor la contaba como
  cita falsa. No había invención ninguna: el prompt prohibía citar señales
  no disponibles y esa prohibición era el error, porque impedía al agente
  fundamentar su mejor decisión con el hecho que la motivaba. Se cambiaron
  el prompt y el auditor, no el modelo. Sirve de aviso sobre la métrica:
  una tasa de fidelidad baja puede estar midiendo un auditor mal diseñado
  en vez de un modelo que miente.
- El test de frontera de la tolerancia encontró que la comparación en coma
  flotante era impredecible justo en el límite: `0.700 - 0.689` da
  `0.011000000000000010`, que quedaba fuera de una tolerancia de `0.011`
  por representación binaria, no por discrepancia real. Se añadió un
  margen de `1e-9`.
