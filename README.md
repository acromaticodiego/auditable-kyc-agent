# Agente de verificación de identidad (KYC)

Un usuario sube la foto de su cédula y una selfie. Un agente de IA decide
**aprobar**, **rechazar**, **escalar a revisión humana** o **solicitar un
reenvío**, razonando sobre varias señales a la vez y explicando la decisión
con fundamentos que citan la señal concreta que los sostiene.

> **Estado: en construcción.** Funcionan el contrato de decisión, la
> verificación de citas, el cliente del modelo con caché y presupuesto, la
> lectura de la MRZ, el generador de cédulas sintéticas y un catálogo de
> 18 casos etiquetados. Falta lo que los une: OCR, reconocimiento facial y
> el endpoint de verificación.

## La idea

El protagonista es el agente, no la visión por computador. El OCR y la
similitud facial son dos fuentes de señal entre varias; el valor está en
cómo se combinan cuando no coinciden, y en que la explicación resultante
se pueda **auditar en lugar de leer y confiar**.

De ahí la restricción de diseño que atraviesa todo el proyecto: el agente
no devuelve un párrafo. Devuelve una decisión estructurada cuyos
fundamentos citan cada uno un identificador de señal y el valor que le
atribuyen. El sistema comprueba después cada cita contra el valor real.
Una explicación que cite una señal inexistente, o que le atribuya un valor
que no tenía, queda registrada como cita inválida.

## Cómo levantarlo

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

La API queda en <http://localhost:8000/docs> y Postgres en el puerto 5434
del anfitrión (5432 y 5433 ya los ocupa otro proyecto de esta maquina).

```powershell
Invoke-RestMethod http://localhost:8000/health
docker compose exec api pytest -q
```

## Decisiones de diseño

Las decisiones que costaron discusión están en [docs/adr/](docs/adr/), con
lo que se descartó y por qué.

- [ADR-0001](docs/adr/0001-presupuesto-de-peticiones-del-agente.md) — por
  qué las señales baratas se precalculan y solo las caras son herramientas
  del agente.
- [ADR-0002](docs/adr/0002-explicacion-auditable.md) — por qué la
  explicación cita señales con su valor y esas citas se verifican, en vez
  de ser prosa que hay que creerse.
- [ADR-0003](docs/adr/0003-la-mrz-como-senal-dura.md) — por qué se lee la
  MRZ del reverso, qué detecta de verdad y qué no.

## Métricas

El proyecto se juzga por dos números, no por la demo:

1. **Fidelidad de las citas.** Proporción de decisiones cuya explicación
   cita alguna señal inexistente o con el valor alterado.
2. **El agente frente a una línea base de reglas fijas.** Un árbol de
   decisión con umbrales duros, medido sobre el mismo conjunto reservado,
   para responder si el agente aporta algo o solo lo cuenta mejor.

Los umbrales y el prompt se ajustan sobre una mitad del conjunto; la otra
mitad no se toca hasta la medición final. Cualquier cifra publicada aquí
irá acompañada del tamaño de muestra y de si el corte se eligió sobre esos
mismos datos.

## Lo que se ha medido hasta ahora

Con honestidad sobre el tamaño de muestra, que por ahora es diminuto:

- **3 casos ejecutados contra la API real** con `gemini-3.1-flash-lite`,
  una petición cada uno. Los 3 respetaron el esquema de respuesta y
  decidieron lo razonable: aprobar el caso claro, pedir reenvío en el
  ambiguo y en el de captura mala. **9 citas, las 9 válidas.** Tres casos
  construidos a mano no permiten afirmar nada sobre fiabilidad; solo
  descartan que el enfoque sea inviable.
- **Una ejecución anterior destapó un fallo del verificador, no del
  modelo.** Citó la ausencia de un campo ilegible y el auditor la contaba
  como cita falsa. Ver ADR-0002.
- **El cupo diario es de 20 peticiones por modelo**, confirmado por el
  cuerpo del 429 (`limit: 20`) y por seguir rechazando tras 140 segundos,
  lo que descarta que sea una ventana por minuto.
- **Un 503 de sobrecarga consume cupo.** Doce peticiones fallidas en dos
  minutos agotaron el día entero sin producir una decisión. Ver ADR-0001.

## El conjunto de evaluación

18 casos con la decisión correcta anotada y **el motivo escrito para poder
discutirse**: 4 legítimos, 6 manipulados, 2 caducados y 6 de captura mala.
Cada uno declara tres cosas distintas que es tentador mezclar: qué debería
decidir el sistema *con la información que tiene*, si el documento es falso
de verdad, y si esa falsedad deja algún rastro visible.

De ahí `fraude-coherente-indetectable`: un documento falso cuya MRZ se
recalculó entera. **Su decisión correcta es aprobarlo**, porque nada en él
permite saberlo. Está en el conjunto para medir el techo del sistema en vez
de esconderlo; un conjunto donde todos los fraudes se detectan mide ese
techo y lo llama acierto.

La partición en calibración y reservado **la impone la herramienta**: pedir
el reservado sin declarar que es la medición final lanza `HoldoutLocked`.
Mirarlo una vez «para ver cómo va» no deja rastro, así que no puede
depender de acordarse.

## Limitaciones

Esta sección crecerá conforme haya resultados que la llenen. Hoy:

- Un solo tipo de documento (cédula colombiana digital de policarbonato).
  No hay nada que sugiera que generalice a otros formatos.
- El conjunto de evaluación será mayoritariamente sintético. Los
  documentos reales usados para calibrar el OCR no se publican.
- La sonda se completó con `gemini-3.1-flash-lite` porque el cupo de
  `gemini-3.5-flash` ya estaba agotado. Para una sonda de contrato da
  igual, pero **ninguna medición de acierto podrá mezclar modelos**.
- La API devuelve 503 con mucha frecuencia; hubo que probar cinco modelos
  para encontrar uno que respondiera.
- El contador de presupuesto empezó a existir después de haberse gastado
  el cupo del primer día, así que esa cuenta se sembró a mano.
- Los 18 casos son variantes de **una sola identidad sintética**, sin una
  foto real de por medio. Ninguna medida hecha sobre ellos dice nada sobre
  documentos reales.
- La partición quedó desbalanceada: 2 fraudes en calibración y 4 en el
  reservado. Con 18 casos el hash no reparte fino; se corregirá creciendo
  el catálogo, no tocando la partición.
- El retrato es un marcador, no una cara. Hasta que haya fotos reales, la
  similitud facial no existe como señal.
- La API rechaza modelos que su propio `ListModels` sigue listando, de
  modo que elegir modelo automáticamente no es fiable.
