"""Medidas de calidad de imagen.

Son senales de produccion: entran en el prompt del agente como evidencia
sobre si la captura permite opinar.  Viven aqui y no en el generador
sintetico porque el generador solo las usa para comprobarse a si mismo.

Todas son deterministas y baratas, asi que se calculan siempre (ver
ADR-0001) y no cuestan cupo de la API.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def sharpness(image: Image.Image) -> float:
    """Varianza del laplaciano: cuanto mas alta, mas nitida la imagen.

    Es la medida clasica de desenfoque y tiene una trampa conocida que
    conviene tener presente antes de usarla como senal: **depende del
    contenido**.  Una foto nitida de una superficie lisa puntua bajo, y
    una foto movida de algo muy texturado puntua alto.  Por eso el valor
    absoluto no significa nada por si solo; lo que informa es compararlo
    entre capturas del mismo tipo de documento, que es justo el caso aqui.

    El umbral que separe "nitida" de "borrosa" tendra que elegirse sobre
    la mitad de calibracion del conjunto y medirse sobre la reservada.  No
    se fija en este modulo a proposito.
    """
    gray = _gray(image)
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0

    # Laplaciano por diferencias finitas, sin dependencias de vision.
    laplacian = (
        -4.0 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return float(laplacian.var())


def saturated_fraction(image: Image.Image, threshold: int = 250) -> float:
    """Proporcion de pixeles casi blancos.

    Detecta el reflejo del policarbonato, que es la forma mas comun de que
    una cedula real salga ilegible: el plastico devuelve el flash y se come
    un campo entero.  Un reflejo no baja la nitidez global -- puede incluso
    subirla por el borde duro del brillo -- asi que el desenfoque no lo ve.
    """
    gray = _gray(image)
    return float((gray >= threshold).mean())


def glare_contrast(
    image: Image.Image, grid: int = 6, threshold: int = 250
) -> float:
    """Cuanto destaca la zona mas saturada sobre el resto de la imagen.

    Nace de que `saturated_fraction` **no sirve** para detectar reflejos, y
    eso se descubrio midiendo, no razonando.  Sobre una cedula sintetica,
    una compresion JPEG a calidad 8 daba un 26,3 % de pixeles saturados
    frente al 17,4 % de un reflejo de verdad: la compresion blanquea el
    fondo entero y una medida global la confunde con un brillo.  Ningun
    umbral entre 250 y 255 arreglaba el solapamiento.

    La diferencia real entre las dos cosas es espacial: un reflejo es una
    mancha localizada y la compresion aclara por todas partes.  Asi que se
    divide la imagen en una rejilla **de bloques solapados** y se devuelve
    la distancia entre el bloque mas saturado y la mediana de los bloques.
    Un reflejo dispara un bloque y deja el resto a cero; un JPEG sube todos
    por igual.  El solapamiento no es un refinamiento opcional: sin el, el
    mismo reflejo puntuaba casi cuatro veces menos cuando caia sobre una
    frontera de la rejilla.

    Tamano de muestra y honestidad sobre el numero: se midio sobre **12
    imagenes sinteticas derivadas de una sola cedula** -- seis reflejos del
    mismo radio en posiciones distintas (0,552 a 0,824) y seis capturas sin
    reflejo: original, desenfoque, baja resolucion, giro y dos compresiones
    (0,000 a 0,253).

    Ese margen esta medido sobre los mismos datos que inspiraron la medida,
    asi que es **optimista por construccion** y no vale como tasa de
    acierto.  Son ademas variantes de una unica cedula sintetica, sin una
    sola foto real de por medio.  Por eso esta funcion no fija ningun
    umbral: devuelve el numero, y el corte se elegira sobre la mitad de
    calibracion del conjunto para medirse sobre la reservada.
    """
    gray = _gray(image)
    height, width = gray.shape
    if height < grid or width < grid:
        return 0.0

    saturated = gray >= threshold
    block_height, block_width = height // grid, width // grid
    # Los bloques se solapan a medio paso.  Con una rejilla sin solapar, el
    # mismo reflejo daba entre 0,218 y 0,822 segun donde cayera: al quedar
    # justo sobre una frontera se repartia entre cuatro celdas y ninguna
    # destacaba.  Un factor de casi cuatro por un artefacto de la rejilla,
    # no por la imagen.
    step_y, step_x = max(1, block_height // 2), max(1, block_width // 2)

    fractions = [
        saturated[top : top + block_height, left : left + block_width].mean()
        for top in range(0, height - block_height + 1, step_y)
        for left in range(0, width - block_width + 1, step_x)
    ]
    return float(max(fractions) - float(np.median(fractions)))


def mean_brightness(image: Image.Image) -> float:
    return float(_gray(image).mean())
