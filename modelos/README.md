# Modelos

Aquí van los pesos de los modelos de visión. **Ningún fichero de pesos se
versiona**: el repositorio se mantiene clonable en segundos y los binarios
grandes no se revisan en un *pull request*, así que en git no aportan nada
y sí estorban. La regla está en `.gitignore`, y quitarla es una línea si
algún día se prefiere lo contrario.

## Qué hay que poner aquí

| fichero | qué es | de dónde sale |
|---|---|---|
| `rostros.pt` | Detector de rostros YOLOv8, ~22 MB | Entrenado en el proyecto `backend_detector`; se copia desde su carpeta `modelos/` |

El **embedder** de ArcFace no va aquí. InsightFace descarga el paquete
`buffalo_l` por su cuenta a `~/.insightface/models/` la primera vez que se
carga, así que no es un fichero que se coloque a mano; lo que sí hay que
comprobar es que esa descarga ocurra **dentro del contenedor**, porque
Kaspersky intercepta TLS en el anfitrión.

## Por qué el detector y el embedder son dos cosas distintas

Es la confusión fácil y cambia lo que se puede medir:

- El **detector** (`rostros.pt`) responde *¿hay una cara aquí y dónde?*
- El **embedder** (ArcFace) responde *¿estas dos caras son la misma
  persona?*, y para eso necesita **dos caras de verdad** que comparar.

Tener los dos modelos no crea las caras. Ver la nota de abajo.

## Lo que falta para que la señal facial exista

Hoy el retrato de la cédula sintética es **un rectángulo gris**, un
marcador de posición, no una cara. El detector no encontraría nada en él y
el embedder no tendría qué comparar. Tampoco hay selfies: cada caso del
conjunto de evaluación son dos imágenes, anverso y reverso.

Así que conectar el reconocedor es la parte fácil —el código del otro
proyecto sirve casi tal cual— y lo que hace falta decidir es **de dónde
salen las caras** que se pintan en el documento y se envían como selfie.
Sin eso, `facial.similarity` sería una señal que siempre sale *no
disponible*, que es peor que no tenerla: da la impresión de que el sistema
mira la cara cuando no la mira.

## Procedencia y licencias

Todo modelo que se añada aquí lleva su origen y su licencia escritos en
esta tabla. Un peso de dudosa procedencia en un sistema que decide sobre la
identidad de personas es un problema legal antes que técnico, y descubrirlo
tarde cuesta mucho más que anotarlo ahora.
