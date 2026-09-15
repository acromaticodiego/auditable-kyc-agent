# ADR-0005: La similitud facial se entrega cruda, sin umbral

- **Fecha:** 2026-09-15
- **Estado:** aceptada

## Contexto

El reconocimiento facial devuelve un número entre −1 y 1: cuánto se parecen
la cara impresa en el documento y la de la selfie. Convertir ese número en
una decisión exige un corte —*por encima de X son la misma persona*— y ese
corte es lo que decide a quién se deja entrar y a quién se rechaza.

Medido sobre fotos reales (44 identidades, ver
[docs/caras-para-la-senal-facial.md](../caras-para-la-senal-facial.md)):

| | misma persona | personas distintas, máximo |
|---|---|---|
| foto contra foto | 0,794 *(n=1)* | 0,246 *(n=989)* |
| pintada en la cédula contra selfie | 0,807 *(n=1)* | 0,263 *(n=1.935)* |
| cédula degradada | 0,762 *(n=1)* | 0,256 *(n=1.935)* |

Hay medio punto de hueco. La tentación evidente es fijar el corte en 0,4 y
convertir la señal en un booleano: *la cara coincide, sí o no*.

## Decisión

**El pipeline entrega `facial.similarity` como el número crudo.** No hay
umbral en la señal. El agente recibe el valor, la descripción que explica
qué significa y las cifras de arriba con sus tamaños de muestra, y decide.

El motivo es el que atraviesa todo el proyecto: **medir y decidir son dos
cosas, y mezclarlas esconde la segunda dentro de la primera.** Un umbral
metido en el pipeline sería una decisión disfrazada de medición. Nadie lo
vería al leer la señal, no aparecería en los fundamentos de la explicación,
y el auditor de citas no tendría nada que verificar: el agente citaría «la
cara coincide = true» y esa cita sería fiel aunque el corte fuera absurdo.

Con el número crudo, en cambio, el agente que aprueba tiene que citar
`facial.similarity = 0.81` y esa cifra se contrasta contra la real. Si
alguien discute la decisión seis meses después, el expediente guarda el
valor, no el veredicto de un corte que a lo mejor ya cambió.

La línea base de reglas fijas **sí** necesita un corte, y lo tiene escrito
en su propio módulo, que es donde corresponde: es un sistema que decide sin
razonar y su umbral es parte de su definición.

## El segundo motivo: el umbral que tendríamos no vale

Aunque quisiéramos fijarlo, no tenemos con qué. **El lado genuino tiene
n=1.** Una sola persona aportó dos fotos suyas.

Ese único punto y los 1.935 impostores dicen dos cosas muy distintas:

- que dos desconocidos no pasan de 0,26 está **medido**, con 44 personas
  reales;
- a cuántos clientes legítimos rechazaría un corte en 0,4 es **desconocido**.
  Hace falta mucha gente fotografiada dos veces, en días distintos, con
  gafas y sin ellas, con barba y sin ella, con luz mala. Eso no existe aquí.

Un corte elegido con un punto de un lado y dos mil del otro sería una
conjetura con formato de número, y este proyecto ya tiene escrito lo que
piensa de esos.

## Por qué el documento y la selfie se leen con criterios distintos

La cédula lleva un **retrato fantasma**: el mismo rostro repetido en pequeño
y desvaído, que los documentos reales usan como medida antifraude. El
detector encuentra los dos, y la primera versión del lector rechazaba la
imagen por ambigua — con lo que la señal facial no llegaba a calcularse
nunca sobre un documento de verdad. Se descubrió al probarla, no al
diseñarla.

La solución no es la misma en los dos lados:

- En el **anverso** se admite quedarse con la cara más grande. El fantasma
  es por diseño mucho menor: medido en el generador, 113 px contra 39, casi
  el triple. Esa diferencia es una propiedad del formato del documento, no
  una corazonada. Si las dos caras fueran de tamaño comparable, no sería un
  fantasma y se rechaza igual.
- En la **selfie** se exige una sola cara. Ahí quedarse con la más grande sí
  sería una corazonada, y de las caras: dejaría pasar a quien sostiene el
  documento de otro con el dueño detrás, o al revés.

## Sin cara, la señal no existe

No hay valor por defecto. Si no se aporta selfie, si no se detecta cara o si
la imagen es ambigua, la señal sale **no disponible con el motivo escrito**.

Inventar un `0.0` sería peor que no tener la señal: un cero es una
afirmación —*estas caras no se parecen en nada*— y en un KYC se leería como
que la selfie no es de la persona del documento. Sería acusar a alguien de
suplantación por una foto que nadie pudo procesar.

El motivo concreto importa porque lleva al agente a sitios distintos: que
falte la selfie es un problema de la solicitud, que no se detecte cara en el
anverso apunta a una foto mala del documento, y que no se detecte en la
selfie se arregla pidiendo otra.

## Alternativas descartadas

**Devolver un booleano `facial.match`.** Es lo cómodo para quien consume la
señal y es exactamente lo que este proyecto no hace. Ver arriba.

**Devolver el número y además el booleano.** Parece un término medio y es
peor que cualquiera de los dos: el agente tendería a citar el booleano por
ser más fácil de justificar, y el número quedaría de adorno dando la
impresión de que la decisión se tomó mirándolo.

**Usar el detector YOLO entrenado en el otro proyecto** (`rostros.pt`).
Arrastra `ultralytics`, `torch` y `torchvision`: unos 2 GB de imagen para
una pieza que aquí no es la protagonista. SCRFD viene dentro de `buffalo_l`
y corre sobre `onnxruntime`. El lector está detrás de una clase por si algún
día compensa cambiarlo.

**Normalizar la similitud a un porcentaje.** Un 80 % invita a leerse como
«ocho de cada diez veces acierta», que no es lo que significa un coseno. Se
deja el número tal cual, con su rango escrito en la descripción de la señal.

## Lo que queda sin comprobar

- El lado genuino sigue en n=1. Cada segunda foto de una persona ya
  presente lo mejora directamente.
- Todas las fotos son de conocidos, hechas con móviles. No representan la
  variedad de edad, tono de piel ni condiciones de captura de un sistema en
  producción, y **un sistema facial se comporta de forma desigual entre
  grupos demográficos**: medirlo exigiría un conjunto que aquí no hay, y no
  medirlo no es lo mismo que no tener el problema.
- El documento **sube** el máximo impostor de 0,246 a 0,263. Con medio punto
  de hueco no importa; con material más difícil sí importaría, y conviene
  recordar que el renderizado empuja en la dirección incómoda.
