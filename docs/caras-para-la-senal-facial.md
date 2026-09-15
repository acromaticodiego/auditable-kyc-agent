# Caras reales para medir la señal facial

> Este documento vive en `docs/` y no junto a las fotos porque
> `data/real/` está en `.gitignore` entero: cualquier cosa que se dejara
> ahí se perdería al clonar, instrucciones incluidas.

**Nada de esta carpeta entra al repositorio.** `data/real/` está en
`.gitignore` desde el principio del proyecto, por la misma razón por la que
no se publican los documentos reales usados para calibrar el OCR: son datos
de personas identificables. Lo que se publica son **los números medidos**,
nunca el material.

## Qué poner aquí

Una carpeta por identidad, con dos fotos:

```
data/real/caras/
  persona-01/
    documento.jpg     <- la que se pinta en el retrato de la cédula
    selfie.jpg        <- la que se envía como selfie
  persona-02/
    ...
```

Los nombres de carpeta son `persona-01`, `persona-02`… **a propósito**: no
hace falta el nombre real de nadie para medir una similitud, y un
identificador neutro evita que un volcado de depuración acabe conteniendo
nombres de personas.

## Cuántas hacen falta

Los dos lados de la medida no cuestan lo mismo:

- Los **pares impostores** salen solos: cada dos personas distintas forman
  uno, así que N identidades dan N×(N−1) pares. Con 44 personas salieron
  casi dos mil.
- Los **pares genuinos** exigen **dos fotos distintas de la misma
  persona**, y ahí no hay atajo. Cada persona que aporte una segunda foto
  suma uno.

La primera medición se hizo con 44 identidades y **un solo par genuino**,
porque el material llegó con una foto por cabeza. Eso permite decir cuánto
se parecen dos desconocidos y no permite decir a cuántos clientes legítimos
se rechazaría. Cada segunda foto que llegue mejora justo ese lado.

## Cómo tienen que ser

La medida solo vale si las dos fotos de cada persona **no son la misma foto
recortada de dos maneras**. Si lo fueran, la similitud saldría altísima y
estaríamos midiendo que dos copias de una imagen se parecen, no que un
sistema reconoce a una persona.

- `documento.jpg`: frontal, expresión neutra, fondo liso, buena luz. Como
  una foto de carné.
- `selfie.jpg`: **otra sesión, otro día si es posible**, con móvil, luz
  distinta, ángulo distinto. Es lo que llega de verdad en un KYC.

Formato: JPG o PNG, la cara ocupando una parte razonable del encuadre, al
menos unos 200 píxeles de lado en la región del rostro.

## Consentimiento

Cada persona cuyas fotos estén aquí tiene que haber dicho que sí, sabiendo
para qué es. No es burocracia: un proyecto de verificación de identidad que
recopila caras sin permiso se contradice a sí mismo, y es lo primero que
preguntaría cualquiera que revise esto en una entrevista.

Si alguien retira su consentimiento, se borra su carpeta y se vuelve a
medir. Por eso los números publicados llevan siempre la fecha y el tamaño
de muestra: para que se sepa sobre qué se calcularon.

## Qué pasa mientras esto esté vacío

La señal `facial.similarity` se reporta como **no disponible**, con el
motivo escrito, y el agente decide sin ella. No se inventa un valor por
defecto: una similitud inventada es exactamente la clase de número que este
proyecto existe para no producir.
