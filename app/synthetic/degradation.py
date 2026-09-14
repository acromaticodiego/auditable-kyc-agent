"""Degradaciones de captura para el conjunto de evaluacion.

Simulan las formas en que una foto de una cedula sale mal en la vida real:
movimiento, reflejo del policarbonato sobre un campo, encuadre que se come
un borde, camara mala, compresion agresiva.

Cada degradacion tiene que **degradar de verdad y de forma medible**.  Una
funcion que aplique un filtro suave y deje la imagen practicamente igual
produciria casos etiquetados como "captura mala" que en realidad se leen
perfectamente, y el conjunto mediria lo contrario de lo que dice medir.
Por eso cada una tiene un test que comprueba el efecto con una medida, no
con la vista.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter


def blur(image: Image.Image, radius: float = 4.0) -> Image.Image:
    """Desenfoque de movimiento o de foco."""
    return image.filter(ImageFilter.GaussianBlur(radius=radius))


def downscale(image: Image.Image, factor: float = 4.0) -> Image.Image:
    """Camara mala: se reduce y se vuelve a ampliar, perdiendo detalle.

    Se devuelve al tamano original a proposito.  Si se devolviera pequena,
    el resto del sistema podria compensar reescalando y la degradacion se
    notaria menos de lo que se pretende.
    """
    small = image.resize(
        (max(1, int(image.width / factor)), max(1, int(image.height / factor))),
        Image.Resampling.BILINEAR,
    )
    return small.resize(image.size, Image.Resampling.BILINEAR)


def glare(
    image: Image.Image,
    center: tuple[float, float] = (0.5, 0.5),
    radius: float = 0.22,
    intensity: float = 1.0,
) -> Image.Image:
    """Reflejo del policarbonato sobre una zona.

    `center` y `radius` van en fraccion del ancho para que el mismo caso se
    pueda aplicar a cualquier tamano de imagen.  El reflejo se suma con un
    perfil suave: un circulo blanco de bordes duros seria mas facil de
    detectar que un reflejo real y haria el caso mas sencillo de lo debido.
    """
    array = np.asarray(image.convert("RGB"), dtype=np.float64)
    height, width = array.shape[:2]

    ys, xs = np.mgrid[0:height, 0:width]
    center_x, center_y = center[0] * width, center[1] * height
    distance = np.sqrt((xs - center_x) ** 2 + (ys - center_y) ** 2)
    falloff = np.clip(1.0 - (distance / (radius * width)) ** 2, 0.0, 1.0)

    array += (255.0 - array) * (falloff * intensity)[:, :, None]
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))


def occlude(
    image: Image.Image,
    box: tuple[float, float, float, float],
    color: tuple[int, int, int] = (238, 242, 246),
) -> Image.Image:
    """Tapa una region, en fracciones del tamano.

    Sirve para el caso en que un dedo, una sombra dura o un recorte dejan
    un campo concreto ilegible sin estropear el resto del documento.
    """
    copy = image.convert("RGB").copy()
    left, top, right, bottom = box
    region = (
        int(left * copy.width),
        int(top * copy.height),
        int(right * copy.width),
        int(bottom * copy.height),
    )
    copy.paste(Image.new("RGB", (region[2] - region[0], region[3] - region[1]), color), region[:2])
    return copy


def blur_region(
    image: Image.Image,
    box: tuple[float, float, float, float],
    radius: float = 3.0,
) -> Image.Image:
    """Emborrona solo una zona, en fracciones del tamano.

    Sirve para el caso en que un campo concreto queda ilegible mientras el
    resto del documento se lee perfectamente: el OCR entonces devuelve algo
    para ese campo, con confianza baja, y hay que decidir si esa
    discrepancia es del documento o de la lectura.
    """
    copy = image.convert("RGB").copy()
    region = (
        int(box[0] * copy.width),
        int(box[1] * copy.height),
        int(box[2] * copy.width),
        int(box[3] * copy.height),
    )
    copy.paste(copy.crop(region).filter(ImageFilter.GaussianBlur(radius)), region[:2])
    return copy


def crop_edge(image: Image.Image, side: str = "bottom", fraction: float = 0.12) -> Image.Image:
    """Encuadre que se come un borde del documento.

    El resultado es mas pequeno: el campo no esta borroso, sencillamente
    **no esta**.  Esa diferencia importa, porque lleva a pedir un reenvio
    por un motivo distinto que el desenfoque.
    """
    width, height = image.size
    cuts = {
        "top": (0, int(height * fraction), width, height),
        "bottom": (0, 0, width, height - int(height * fraction)),
        "left": (int(width * fraction), 0, width, height),
        "right": (0, 0, width - int(width * fraction), height),
    }
    if side not in cuts:
        raise ValueError(f"lado desconocido: {side!r}")
    return image.crop(cuts[side])


def jpeg_artifacts(image: Image.Image, quality: int = 12) -> Image.Image:
    """Compresion agresiva, como la de una foto reenviada por mensajeria."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def rotate(image: Image.Image, degrees: float = 4.0) -> Image.Image:
    """Documento torcido sobre la mesa."""
    return image.rotate(
        degrees, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(235, 240, 245)
    )
