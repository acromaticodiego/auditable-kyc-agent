# ADR-0003: La MRZ del reverso se lee y se verifica, y es la única señal con certeza

- **Fecha:** 2026-09-14
- **Estado:** aceptada

## Contexto

Todas las señales del proyecto son probabilísticas. La similitud facial da
un número entre 0 y 1 cuyo umbral es discutible; la confianza del OCR mide
lo seguro que está un modelo, no lo cierto que es el dato; el desenfoque es
una heurística. El agente razona bien sobre ese material, pero no hay nada
en él que se pueda afirmar con certeza.

La cédula colombiana de policarbonato lleva en el reverso una **MRZ en
formato TD1 de ICAO 9303**: tres líneas de 30 caracteres con dígitos de
control calculados sobre los propios datos con el algoritmo de pesos
7-3-1. Además, el número de identidad y las fechas de nacimiento y
expiración aparecen **dos veces en el documento**: impresas en el anverso y
codificadas dentro de la MRZ.

Esto no se descubrió leyendo la especificación, sino mirando el reverso de
una cédula real. La mayoría de los proyectos de KYC de portafolio ignoran
la MRZ y se quedan con el OCR del anverso.

## Decisión

Se lee la MRZ, se verifican sus cuatro dígitos de control y se cotejan los
campos duplicados contra lo leído en el anverso. De ahí salen señales
**deterministas**: no hay umbral que elegir ni muestra sobre la que
calibrar, o los dígitos cuadran o no cuadran.

**Un dígito que no cuadra no rechaza por sí solo.** La decisión sigue
siendo del agente. El motivo es que una MRZ mal leída y una MRZ manipulada
producen el mismo síntoma, y confundirlas acusaría de falsificar a quien
solo hizo una foto movida. Por eso el código separa dos cosas que es
tentador juntar:

- `MrzError` — la MRZ **no tiene forma de MRZ**: longitud equivocada,
  caracteres imposibles, líneas de menos. Es una lectura fallida, un
  problema de captura, y lleva a pedir un reenvío.
- Un `CheckResult` que no cuadra — la MRZ se leyó perfectamente y **sus
  propios datos no son consistentes**. Eso sí es una señal sobre el
  documento.

## Alternativas descartadas

**Ignorar la MRZ y quedarse con el OCR del anverso.** Es lo que hace casi
todo el mundo, y desperdicia la única fuente de certeza del documento.

**Rechazar automáticamente cuando un dígito no cuadra, sin pasar por el
agente.** Tentador porque la señal es dura. Descartado por lo anterior: la
dureza está en la aritmética, no en la lectura que la alimenta. Un OCR que
confunde un 8 con un 6 en la MRZ produce exactamente la misma evidencia
que un falsificador descuidado.

**Leer el QR del reverso.** Contiene presumiblemente los mismos datos
firmados por la Registraduría, lo que sería una verificación mucho más
fuerte que la MRZ. Descartado por ahora: sin la clave pública de la
entidad no se puede validar la firma, y sin validarla el QR no aporta nada
que la MRZ no dé ya. Queda anotado como el siguiente paso obvio si
apareciera documentación pública.

**Calcular los dígitos con una librería de MRZ.** Son treinta líneas de
aritmética y escribirlas permite separar `MrzError` de un dígito que falla,
que es justo la distinción que las librerías tienden a aplanar en una
excepción única.

## Consecuencias

- **Una manipulación nunca rompe una sola comprobación.** El dígito
  compuesto cubre los mismos campos que ya tienen dígito propio, más los
  opcionales. Cambiar el número de documento rompe dos comprobaciones;
  cambiar solo el NUIP —que no tiene dígito propio— rompe el compuesto.
  Falsificar sin dejar rastro aritmético obliga a recalcular la MRZ entera.
- **Limitación grande, y hay que decirla:** una MRZ inventada desde cero es
  perfectamente consistente consigo misma. Esto detecta la **manipulación
  de un documento real**, no una falsificación completa bien hecha. Que los
  cuatro dígitos cuadren no prueba en absoluto que el documento sea
  auténtico.
- La señal solo existe cuando el reverso es legible. En una captura mala no
  hay MRZ, y el sistema vuelve a depender enteramente del material
  probabilístico.
- **Riesgo de que esta señal aplaste al agente.** Es tan concluyente cuando
  habla que puede convertir la decisión en un `if`, y entonces el proyecto
  demostraría bastante menos de lo que pretende. Hay que vigilarlo al medir
  contra la línea base de reglas: si el árbol de reglas iguala al agente en
  todos los casos con MRZ legible, la conclusión honesta es que el agente
  aporta solo donde la MRZ falta.
- La implementación se verificó una vez contra **una cédula real** (los
  cuatro dígitos cuadraban) y contra el **ejemplo canónico de ICAO 9303**,
  que es el que vive en los tests. El documento real no se versiona.
