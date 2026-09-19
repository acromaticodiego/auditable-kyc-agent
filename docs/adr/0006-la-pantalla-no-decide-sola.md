# ADR-0006: La pantalla sirve decisiones reales, y no decide sola

- **Fecha:** 2026-09-19
- **Estado:** aceptada

## Contexto

El sistema entrega expedientes en JSON. Contienen todo lo que hace falta —la
decisión, los fundamentos con la señal que cada uno cita, el veredicto de la
auditoría de cada cita y las 28 señales tal como se midieron— pero hay que
leérselos enteros para ver lo único que distingue a este proyecto de
cualquier demostración de *un modelo decide algo*: que lo que el agente
afirma **se comprueba**.

Una pantalla resuelve eso. Una columna que diga, cita por cita, si lo que se
afirmó era cierto se entiende de un vistazo.

Pero una pantalla se abre delante de alguien y se pulsa muchas veces. Y cada
decisión del agente es una petición de las **veinte del día** que da el plan
gratuito (ver [ADR-0001](0001-presupuesto-de-peticiones-del-agente.md)). Una
pantalla que decidiera en vivo con cada clic se comería en dos minutos el
cupo reservado para la medición del día. No es una hipótesis: en este mismo
proyecto ya se perdieron tres peticiones por lanzar una herramienta creyendo
que solo miraba.

Hay además un segundo problema, y es el que de verdad tiene filo. Existe
`app/evaluation/rehearsal.py`, un doble del modelo que responde lo que
respondería la línea base de reglas fijas, con el formato del contrato y
sin gastar nada. Enchufarlo a la pantalla era **una línea de código** y
resolvía el cupo de golpe.

## Decisión

**La pantalla sirve solo respuestas que ya están en caché, y cuando un caso
no lo está se niega y dice dónde se mide.** Nunca pide una decisión por su
cuenta.

**Y lo que muestra son decisiones reales del modelo, no del doble.** Salen
de disco, pero las produjo `gemini-3.5-flash` ante ese mismo juego de
señales.

Las dos mitades de la decisión se sostienen entre sí. Servir de caché es lo
que permite no mentir sobre el coste; servir decisiones reales es lo que
hace que la pantalla valga algo.

Enchufar el doble habría convertido la pantalla en **una mentira bonita**:
lo que se enseñaría serían las reglas fijas de la línea base disfrazadas de
agente. Peor todavía, sería una mentira que nadie podría detectar mirando la
pantalla, porque el doble produce decisiones válidas según el contrato, con
sus fundamentos y sus citas, y la columna de auditoría las daría por buenas.
El proyecto entero existe para que una afirmación se pueda contrastar; una
pantalla que enseña reglas fijas llamándolas agente es exactamente el fallo
que el resto del sistema está montado para hacer imposible.

El precio de la decisión es real y conviene decirlo: la pantalla **solo
puede enseñar lo que ya se midió alguna vez**. No es una limitación técnica
disimulada, es la consecuencia de no querer que decidir y mirar sean la
misma cosa.

## Un documento traído por quien mira

Trece casos ya cocinados no son una demostración en vivo: en una entrevista
lo que convence es meter algo que no estaba preparado. Así que la pantalla
acepta subir un documento, y ahí el reparto anterior se vuelve explícito.

**Medir es gratis y repetible.** El OCR, los dígitos de control de la MRZ,
los cotejos, la nitidez y las caras son código normal: no piden nada a
ningún modelo. Es lo que se pulsa varias veces mientras se explica.

**Decidir es lo único que gasta, y lo dice antes de hacerlo**: cuántas
peticiones quedan hoy y si esas señales ya están en caché. Es la misma regla
que la de `--gastar` en las herramientas de evaluación, por el mismo motivo
y después del mismo incidente.

Un documento subido se evalúa contra la fecha de **hoy** y no contra la fija
del catálogo, porque es lo que hará en producción. Tiene un efecto que
conviene saber antes de una demostración: casi nunca coincidirá con una
respuesta guardada, así que preguntar costará una petición de verdad. Lo
sensato es medirlo y preguntarlo una vez la víspera; el día de la demo sale
de caché, instantáneo y sin depender de que el proveedor esté en pie.

## Del documento subido se guarda una copia reducida, nunca el original

`POST /verificaciones` no guarda las imágenes: solo su SHA-256. Esa decisión
no puede tener una puerta de atrás por la que sí se queden, y una pantalla
que enseña el documento es exactamente esa puerta.

Se guarda una copia de 720 px de lado largo sobre un original de 1012, en
memoria, con un tope de doce y perdiéndose al reiniciar. Basta para leer un
apellido retocado, que es lo único que hay que poder mirar de cerca. Hay un
test que lo fija: si alguien vuelve a guardar el original, falla.

Que quien revisa vea el documento no es adorno. **El agente no lo ve** —a él
se le mandan solo números— y esa asimetría es el diseño: la persona que
juzga la decisión tiene delante la evidencia que el modelo no tuvo.

## El conjunto reservado no es accesible desde aquí

Aunque sus casos también estén en caché y servirlos no costara nada.

Lo que protege la partición no es el cupo, es **no haber mirado** (ver
`app/evaluation/split.py`). Una pantalla que invita a pulsar es la forma más
fácil que existe de acabar viendo el reservado «solo para ver cómo va», y a
partir de ese momento deja de ser reservado sin que quede rastro.

## Alternativas descartadas

**Enchufar el doble de ensayo.** Una línea de código, responde siempre, a
cualquier cosa, sin cupo. Descartada arriba: enseñaría la línea base
disfrazada de agente y nadie podría notarlo desde la pantalla.

**Decidir en vivo con cada clic.** Es lo que haría cualquier demostración, y
es coherente con lo que el sistema hace en producción. Descartada por el
cupo: veinte peticiones al día son dos minutos de demostración y cero
mediciones. La versión que sí se hizo —autorizar el gasto con un botón que
dice lo que cuesta— conserva lo que tenía de bueno y quita lo que tenía de
caro.

**Guardar el documento subido para poder volver a verlo.** Cómodo, y
convierte una pantalla de demostración en un almacén de documentos de
identidad. Descartada por lo mismo que el resto del sistema no los guarda.

**Una interfaz con React y su compilación.** Descartada por
desproporcionada: es una página. Un fichero HTML servido tal cual y un
endpoint JSON hacen lo mismo sin añadir un paso de construcción, una
dependencia y una carpeta de nodos a un proyecto cuyo argumento es que se
puede leer entero.

**Renderizar la página con Jinja2.** Habría obligado a reconstruir la imagen
para añadir una dependencia, y a cambio de nada: no hay más que una página y
el contenido lo pinta el navegador desde el JSON que ya existía.

## Lo que queda sin comprobar

La pantalla está verificada por sus endpoints y por tests que le inyectan un
cliente cuyo transporte **revienta si alguien lo usa** —uno que devolviera
una respuesta válida haría pasar los tests sin probar la promesa—, y las
mutaciones contra esos tests mueren por el motivo esperado.

Lo que no está comprobado es cómo se ve. No hay ninguna prueba automática
del resultado visual, ni de que quepa sin desplazarse en una resolución
distinta de aquella en la que se miró. Para una página de una demostración
eso es asumible; anotarlo es más honesto que dejar creer que «386 tests en
verde» cubre también lo que se ve.
