"""Deteccion de rostro y embedding, para comparar la selfie con el documento.

El reconocimiento facial es **una senal mas**, no el centro del sistema.
Dice si dos caras son la misma persona; no dice si el documento es
autentico, ni si la persona es quien dice ser cuando el documento es falso.
El agente lo combina con las demas senales, que es donde esta el valor de
este proyecto.

POR QUE SCRFD Y NO EL DETECTOR YOLO PROPIO
------------------------------------------

El paquete `buffalo_l` de InsightFace trae las dos piezas -- SCRFD para
detectar y ArcFace (`w600k_r50`) para el embedding -- y las dos corren sobre
`onnxruntime`.  El detector YOLO entrenado en el otro proyecto arrastraria
`ultralytics`, `torch` y `torchvision`: unos 2 GB de imagen para una pieza
que aqui no es la protagonista.

EL EMBEDDING VIENE NORMALIZADO
------------------------------

ArcFace devuelve un vector de 512 dimensiones y aqui se normaliza a norma 1,
de modo que el **producto escalar entre dos embeddings ES su similitud
coseno**.  Eso evita el error clasico de comparar vectores sin normalizar y
obtener numeros fuera de [-1, 1] que luego alguien interpreta como
porcentajes.

DOS CARAS EN UN DOCUMENTO SON LO NORMAL
---------------------------------------

La cedula lleva un **retrato fantasma**: el mismo rostro repetido en pequeno
y desvaido, que los documentos reales usan como medida antifraude.  El
detector encuentra los dos, y la primera version de este modulo rechazaba
esa imagen por ambigua, con lo que la senal facial nunca llegaba a
calcularse sobre un documento de verdad.

Asi que el documento y la selfie se leen con criterios distintos y por
motivos distintos:

- en el **documento** se admite quedarse con la cara mas grande, porque el
  fantasma es por diseno mucho menor -- medido en el generador: 113 px
  contra 39, casi el triple -- y esa diferencia es una propiedad del
  formato, no una corazonada.  Si las dos caras fueran de tamano
  comparable, no seria un fantasma y se rechaza igual;
- en la **selfie** se exige una sola cara.  Ahi quedarse con la mas grande
  si seria una corazonada, y de las caras: dejaria pasar a quien sostiene
  el documento de otro con el dueno detras, o al reves.

SIN MODELO O SIN CARA, LA SENAL NO EXISTE
-----------------------------------------

No hay valor por defecto.  Si el modelo no esta descargado, si la imagen no
tiene ninguna cara o si es ambigua, la lectura sale **no disponible con el
motivo escrito**, y el agente decide sin ella sabiendo que le falta.
Inventar un 0.0 seria peor que no tener la senal: un cero se lee como "no se
parecen" cuando lo cierto es "no se ha podido mirar".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# Confianza minima del detector para dar una cara por buena.  Es el valor por
# defecto de InsightFace; se deja explicito porque es un corte y los cortes
# de este proyecto se escriben, no se heredan en silencio.
MIN_CONFIDENCE = 0.5

# Tamano al que trabaja el detector.  Mas grande encuentra caras mas
# pequenas y tarda mas; 640 es el compromiso habitual y basta de sobra para
# un retrato de cedula o una selfie.
DETECTION_SIZE = (640, 640)

# Cuanto mas ancha tiene que ser la cara principal que la siguiente para dar
# por hecho que la segunda es el retrato fantasma y no otra persona.  En el
# generador la proporcion medida es 2.9; el corte en 1.8 deja margen para
# una foto torcida sin llegar a aceptar dos caras comparables.
GHOST_RATIO = 1.8


@dataclass(frozen=True)
class FaceReading:
    """Lo que se pudo leer de una imagen, o por que no se pudo."""

    embedding: np.ndarray | None = None
    side: int = 0
    confidence: float = 0.0
    faces_found: int = 0
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.embedding is not None

    def __post_init__(self) -> None:
        if (self.embedding is None) == (self.reason is None):
            raise ValueError(
                "una lectura facial tiene embedding o motivo de ausencia, "
                "nunca las dos cosas ni ninguna"
            )


def similarity(one: FaceReading, other: FaceReading) -> float | None:
    """Similitud coseno entre dos lecturas, o None si falta alguna.

    Devuelve None y no 0.0 a proposito: un cero significa "estas dos caras no
    se parecen en nada", que es una afirmacion, y aqui no hay ninguna que
    hacer.
    """
    if not one.available or not other.available:
        return None
    return float(np.dot(one.embedding, other.embedding))


class FaceReader:
    """Detecta la cara de una imagen y devuelve su embedding normalizado.

    El modelo se carga la primera vez que se usa y no al construir el lector.
    Son unos 200 MB: cargarlos al importar haria que arrancar la API, o
    ejecutar un test que no toca caras, costara esa memoria y ese tiempo sin
    motivo.
    """

    def __init__(
        self, pack: str = "buffalo_l", min_confidence: float = MIN_CONFIDENCE
    ) -> None:
        self._pack = pack
        self._min_confidence = min_confidence
        self._model: Any | None = None

    @property
    def name(self) -> str:
        return f"{self._pack}/scrfd+arcface"

    def _load(self) -> Any:
        if self._model is not None:
            return self._model

        from insightface.app import FaceAnalysis

        model = FaceAnalysis(
            name=self._pack,
            # Se piden solo las dos piezas que se usan. Sin esto InsightFace
            # carga tambien los modelos de puntos faciales y de edad y sexo,
            # que son 150 MB mas y aqui no pintan nada. Ademas, deducir edad
            # y sexo de una cara es justo lo que un sistema de verificacion
            # de identidad NO debe hacer: esos datos estan en el documento.
            allowed_modules=["detection", "recognition"],
        )
        model.prepare(ctx_id=-1, det_size=DETECTION_SIZE)
        self._model = model
        return model

    def read(
        self, source: Path | str | Image.Image, *, allow_ghost: bool = False
    ) -> FaceReading:
        """Lee una cara.  `allow_ghost` solo para el anverso del documento.

        Ver la nota de la cabecera sobre por que el documento y la selfie se
        leen con criterios distintos.
        """
        try:
            model = self._load()
        except Exception as error:  # noqa: BLE001 - falta el modelo, no un fallo logico
            return FaceReading(
                reason=(
                    "no se pudo cargar el modelo facial "
                    f"({type(error).__name__}: {error}). Ver modelos/README.md."
                )
            )

        if isinstance(source, (str, Path)):
            try:
                image = Image.open(source)
                image.load()
            except OSError as error:
                return FaceReading(reason=f"no se pudo abrir la imagen: {error}")
        else:
            image = source

        # InsightFace espera BGR, que es el orden de OpenCV; PIL entrega RGB.
        # Invertirlo mal no revienta, solo empeora la deteccion en silencio,
        # que es la peor forma de equivocarse.
        matrix = np.array(image.convert("RGB"))[:, :, ::-1]

        faces = [
            face
            for face in model.get(matrix)
            if float(face.det_score) >= self._min_confidence
        ]
        faces.sort(key=lambda f: -(f.bbox[2] - f.bbox[0]))

        if not faces:
            return FaceReading(
                faces_found=0, reason="no se detecto ninguna cara en la imagen"
            )

        if len(faces) > 1:
            widest = faces[0].bbox[2] - faces[0].bbox[0]
            second = faces[1].bbox[2] - faces[1].bbox[0]
            es_fantasma = allow_ghost and widest >= GHOST_RATIO * second
            if not es_fantasma:
                return FaceReading(
                    faces_found=len(faces),
                    reason=(
                        f"se detectaron {len(faces)} caras de tamano comparable "
                        "y no hay forma de saber cual es la de la persona que "
                        "se verifica"
                    ),
                )

        face = faces[0]
        vector = np.asarray(face.normed_embedding, dtype=np.float32)
        x1, y1, x2, y2 = (int(v) for v in face.bbox)

        return FaceReading(
            embedding=vector,
            side=min(x2 - x1, y2 - y1),
            confidence=float(face.det_score),
            faces_found=len(faces),
        )
