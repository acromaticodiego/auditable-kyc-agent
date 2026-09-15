# Agente de verificación de identidad (KYC)

Un usuario sube la foto de su cédula y una selfie. Un agente de IA decide
**aprobar**, **rechazar**, **escalar a revisión humana** o **solicitar un
reenvío**, razonando sobre varias señales a la vez y explicando la decisión
con fundamentos que citan la señal concreta que los sostiene.

> **Estado: en construcción.** Funcionan el contrato de decisión, la
> verificación de citas, el cliente del modelo con caché y presupuesto, la
> lectura de la MRZ, el generador de cédulas sintéticas y un catálogo de
> 25 casos etiquetados, el extractor completo (OCR del anverso, MRZ del
> reverso y cotejo entre ambos) y una línea base de reglas fijas ya medida.
> Falta el agente, el reconocimiento facial y el endpoint de verificación.

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

Una verificación completa, con las dos caras del documento:

```powershell
curl.exe -X POST http://localhost:8000/verificaciones `
  -F "anverso=@anverso.png;type=image/png" `
  -F "reverso=@reverso.png;type=image/png"
```

Devuelve `201` con la decisión, su resumen y **cada fundamento junto al
veredicto de su auditoría**. `GET /verificaciones/{id}` añade las 28
señales tal como se midieron, que es lo que permite contrastar las citas
más tarde. Las imágenes no se guardan: solo su SHA-256.

Si el modelo falla o se acaba el cupo, la respuesta **sigue siendo 201**
con `escalate_to_human`. El proveedor falló, pero el sistema decidió; ver
[ADR-0004](docs/adr/0004-que-hace-el-sistema-cuando-el-agente-no-contesta.md).

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
- [ADR-0004](docs/adr/0004-que-hace-el-sistema-cuando-el-agente-no-contesta.md)
  — por qué un agente que no contesta escala a un humano en vez de aprobar
  o rechazar, y cuánto cuesta esa elección.

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

Con honestidad sobre el tamaño de muestra, que por ahora es pequeño:

### Lectura de la MRZ — 70 imágenes (10 identidades × 7 condiciones)

| | primera versión | tras corregir |
|---|---|---|
| lecturas exactas | 1/70 | **46/70** |
| los cuatro dígitos cuadran | 53/70 | 53/70 |
| **cuadran sobre una lectura errónea** | **52/70** | **7/70** |
| sin MRZ legible | 4/70 | 4/70 |

El número que importa es el tercero. De las 53 lecturas que la aritmética
daba por buenas, **52 estaban mal**: el 98 % de las validaciones eran
falsas. La causa era casi siempre un `COL` leído `C0L`, y el motivo de que
pasara inadvertido es estructural: **el país emisor, el sexo, la
nacionalidad y la línea entera de nombres quedan fuera del payload de los
cuatro dígitos de control.** Es el punto ciego del formato TD1.

Corregir esos campos es seguro precisamente porque no participan en ningún
check: una corrección ahí no puede fabricar validez aritmética. Los 7
falsos válidos que quedan están casi todos en la línea de nombres, y ahí no
se corrige nada porque no hay ninguna regla estructural que lo permita sin
inventar.

Advertencias sobre estas cifras: son cédulas sintéticas rectificadas, sin
una sola foto real. Y un detalle que delata lo artificial del banco: un
desenfoque leve (9/10) **lee mejor que la imagen limpia** (7/10), porque
suaviza el aliasing del renderizado. Eso no pasa con documentos de verdad.

### Lectura del anverso — las mismas 70 imágenes

| campo | anclado solo en rótulos | con respaldo por orden |
|---|---|---|
| NUIP | 59/70 | 59/70 |
| apellidos | 30/70 | **60/70** |
| nombres | 23/70 | **59/70** |
| fecha de nacimiento | 30/70 | **60/70** |
| fecha de expiración | 24/70 | **58/70** |
| fecha de expedición | 20/70 | **60/70** |
| sexo | 20/70 | 20/70 |
| nacionalidad | 27/70 | 27/70 |

La extracción se ancla en los rótulos impresos («Apellidos», «Nombres»…)
en vez de en coordenadas fijas, para no construir un lector que funcione
solo con las imágenes de casa. Medirlo destapó que ese anclaje era el
punto débil: con un desenfoque de radio 1,5 los rótulos —gris claro,
cuerpo pequeño— desaparecen y el extractor pasaba de once campos a uno.

La conclusión fácil habría sido «el anverso no se puede leer con esa
calidad», y era falsa: **los valores seguían leyéndose con confianza de 90
y pico**, en negrita y más grandes. El problema no era la imagen sino de
dónde colgaba la lectura. El respaldo usa el orden vertical de los campos,
que en la cédula es fijo, y cada campo viaja marcado con su procedencia:
leído bajo su rótulo, o deducido.

El sexo y la nacionalidad siguen dependiendo de su rótulo y se quedan
donde estaban. Queda medido y sin tapar.

**La confianza de Tesseract sirve**: 0,895 de media en las 403 lecturas
correctas frente a 0,561 en las 27 equivocadas. Es lo único observable en
producción, porque el anverso no tiene forma de delatar una lectura
errónea: no hay dígitos de control ni redundancia, a diferencia de la MRZ.

### El enderezado de la MRZ — 90 imágenes (se añadieron dos giros al banco)

| condición | sin enderezar | con enderezar |
|---|---|---|
| girada 3° | 1/10 exactas | **7/10** |
| girada 6° | 0/10, y 10/10 **sin MRZ reconocible** | **7/10** |
| limpia | 7/10 | 6/10 |
| **total** | 47/90 | **59/90** |

Existe porque pasar el pipeline por la mitad de calibración destapó un
falso positivo grave: **una cédula legítima girada 3° daba los dígitos de
la MRZ descuadrados y una contradicción en la fecha de nacimiento** — las
mismas señales exactas que un fraude. Medido, la MRZ aguantaba hasta 2°.
Las fotos reales de un documento sobre una mesa vienen torcidas por
sistema.

El coste está en la última fila y no se esconde: en imágenes rectas baja de
7 a 6 sobre 10, porque el estimador prefiere 1° donde no hay ninguno.

La primera versión del enderezado probaba ángulos hasta que los dígitos
cuadraran, y **un test la tumbó en el acto**: sobre una MRZ con un dígito
roto a propósito, el barrido encontraba un ángulo donde la lectura validaba
y declaraba válido un documento manipulado. Era un corrector encubierto que
elegía el resultado que le convenía. Ahora el ángulo se estima por
geometría, sin mirar si valida.

### La línea base de reglas fijas — y lo que eso significa

Un árbol de decisión sin modelo de lenguaje, con los umbrales elegidos
mirando **solo la mitad de calibración**:

| | aciertos | explicaciones fieles |
|---|---|---|
| calibración (12 casos) | 9/12 | 12/12 |
| **reservado (13 casos)** | **10/13** | 13/13 |

Con la primera versión del conjunto —18 casos— la línea base sacaba 8/9 en
el reservado. **Ese resultado no era una buena noticia**: significaba que
las señales deterministas resolvían casi todo el conjunto ellas solas, y
que comparar el agente contra ellas no iba a informar de nada. Así que el
paso siguiente no fue construir el agente, sino **ampliar el conjunto con
siete casos que las reglas no pueden resolver**.

Donde falla ahora la línea base es justo donde hace falta juicio: pide otra
foto por un reflejo que no tapa ningún campo, y rechaza de plano una
discrepancia de un solo carácter en el sexo que igual es un fallo del OCR.

El de la izquierda está inflado por construcción: los cortes se eligieron
sobre esos mismos casos. **El de la derecha es la medida.**

El único fallo es `caducado-y-borroso`, que ya estaba etiquetado como
discutible antes de medir nada: las reglas piden otra foto por la nitidez
baja antes de mirar la vigencia, y la etiqueta dice rechazar porque la
fecha sigue leyéndose. No se han tocado los umbrales para arreglarlo — el
reservado queda quemado, y ajustarlo ahora convertiría la próxima medición
en otro número elegido sobre sus propios datos.

El único fallo que viene de la primera versión del conjunto sigue siendo
`caducado-y-borroso`. Los umbrales **no se han tocado** para arreglar
ninguno de los tres: ajustarlos mirando el reservado convertiría la
siguiente medición en otro número elegido sobre sus propios datos.

La línea base cita señales y pasa por el mismo verificador que el agente.
Si pudiera explicarse sin citar nada verificable, la comparación sería
injusta a su favor.

### El agente frente a la línea base — 12 casos de calibración

Primera medición completa del agente sobre el conjunto de **calibración**,
con `gemini-3.1-flash-lite`, los 12 casos contestados. **No es el número
que se publica**: la calibración es donde se ajusta el prompt, y el corte
que vale es el reservado, que sigue sin tocarse.

| | |
|---|---|
| Agente | **8/12** |
| Línea base de reglas fijas, **los mismos 12 casos** | **9/12** |
| Explicaciones fieles | **12/12** |
| Citas verificadas una a una | **38/38 correctas** |

**Un `if/else` le gana al agente.** Y el 9/12 de la línea base todavía está
inflado, porque sus umbrales se eligieron mirando estos mismos casos.

Lo que el agente sí hace impecable es lo que este proyecto dice que
importa: 38 citas, ninguna inventada, ningún valor mal atribuido. La
explicación se sostiene aunque la decisión no siempre acierte.

Con una salvedad que hay que leer pegada a ese `38/38`: **la fidelidad mide
que no mienta, no que lo cuente todo.** El auditor recorre las citas que el
agente hizo; no puede ver las que calló. Un agente que aprueba citando con
toda exactitud que la MRZ es legible, y omitiendo que el apellido no
coincide, saca fidelidad perfecta.

Por eso hay una segunda medida, la **completitud**: si la explicación
menciona todas las señales que jugaban en contra. No juzga la decisión —
citar una señal adversa y aun así aprobar es legítimo y es justo lo que se
quiere poder leer después— sino el silencio. Las condiciones adversas son
adversas *por construcción del dominio* y no por un umbral elegido a ojo:
dos copias del mismo dato que no coinciden, unos dígitos de control que no
cuadran, un documento vencido o con fechas que se contradicen, una MRZ
ilegible.

La definición se eligió **midiendo antes**. La primera versión iba a contar
la omisión solo al aprobar; contar las condiciones sobre los 12 casos la
descartó, porque ninguno de los casos que deben aprobarse tiene una sola
señal adversa y esa versión se habría cumplido sola en los 12 sin medir
nada. La misma cuenta despejó la duda contraria: ningún caso tiene más de
una adversa —seis tienen una y seis ninguna—, así que exigir que se citen
todas no es pedante y deja **seis casos donde de verdad se puede fallar**.

**Todavía no hay cifra de completitud**: la medición de arriba es anterior
a esta métrica, y volver a medirla cuesta otra tanda de 12 peticiones. Sale
en la próxima. Ver [ADR-0002](docs/adr/0002-explicacion-auditable.md).

Los 4 fallos no están repartidos al azar. **Tres de los cuatro son el mismo
comportamiento: escalar en vez de comprometerse** — dos `reject` y un
`request_resubmission` convertidos en `escalate_to_human`. En los dos
fraudes ve la contradicción entre anverso y MRZ, la describe bien, y aun
así pide revisión humana «para descartar manipulación o error de lectura».

El cuarto va en la dirección peligrosa y señala un hueco del prompt: en
`ambiguo-menor-de-edad` **aprobó**. La señal `document.age_years` lleva
escrito que un menor no abre cuenta igual que un adulto, pero el menú de
decisiones del prompt está redactado entero en términos de identidad y
autenticidad. Un documento auténtico de alguien no elegible no tiene
casilla donde caer, y el agente hizo lo coherente con lo que se le dijo.

La primera versión de este informe comparaba el `8/12` del agente con el
`9/12` de la línea base sobre el conjunto entero — denominadores distintos
sobre casos distintos. Ahora los dos números salen siempre sobre los mismos
casos.

### La sonda de contrato — 3 casos

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
- **El contador de cupo falló dos veces, y las dos las pagó el proyecto.**
  Primero cortaba el día en medianoche **UTC** cuando Google lo corta en la
  del **Pacífico**: a las 00:00:29 UTC el contador estrenó día y concedió
  20 peticiones mientras el proveedor seguía contando las anteriores.
  Segundo, la cuenta previa del coste de una tanda ignoraba los reintentos,
  y un 503 cuesta dos peticiones: un plan de 10 gastó 22 y no midió un solo
  caso. Ambos corregidos, con tests que fijan los dos lados de la frontera
  horaria. Por eso existe `scripts/probe_model.py`, que gasta **una**
  petición para saber si un modelo responde hoy antes de fiarle una tanda.

## El conjunto de evaluación

25 casos con la decisión correcta anotada y **el motivo escrito para poder
discutirse**: 4 legítimos, 6 manipulados, 2 caducados, 6 de captura mala y 7 elegidos por lo que las reglas fijas no saben resolver.
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
- Los 25 casos son variantes de **una sola identidad sintética**, sin una
  foto real de por medio. Ninguna medida hecha sobre ellos dice nada sobre
  documentos reales.
- El conjunto vivió 18 casos **sin un solo caso de escalado a revisión
  humana**, midiendo un sistema de tres salidas y llamándolo de cuatro.
- La partición está desbalanceada: 3 fraudes en calibración y 5 en el
  reservado, y el escalado a revisión humana cae 2 contra 1. El hash no
  reparte fino con tan pocos casos; se corrige creciendo el catálogo, no
  tocando la partición.
- El retrato es un marcador, no una cara. Hasta que haya fotos reales, la
  similitud facial no existe como señal.
- La API rechaza modelos que su propio `ListModels` sigue listando, de
  modo que elegir modelo automáticamente no es fiable.
- **La medición de calibración se hizo con `gemini-3.1-flash-lite`, que no
  es el modelo configurado** (`gemini-3.5-flash`). Se llegó a él por
  descarte: 3.5 y 3.8 sin cupo, 3.7 devolviendo 503. Comparar ese 8/12 con
  una medición futura hecha con otro modelo sería comparar dos sistemas.
- **El endpoint no se ha ejercitado en vivo con una respuesta buena del
  modelo.** Contra el servidor levantado sí se comprobó el camino completo
  —subida, OCR, agente, escritura y lectura del registro— pero el agente
  acabó en `out_of_quota`. El camino con decisión real está cubierto por
  tests contra el Postgres de verdad, con el modelo simulado.
- **No hay migraciones.** Las tablas se crean al arrancar; en cuanto haya
  datos que no se puedan perder, esto necesita Alembic.
- No hay autenticación, límite de tamaño de subida ni control de acceso al
  registro. `GET /verificaciones/{id}` lo lee cualquiera que tenga el
  identificador.
