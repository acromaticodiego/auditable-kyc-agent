"""Pruebas de las degradaciones y de las medidas de calidad.

Una degradacion tiene que degradar **de forma medible**.  Si una funcion
aplicara un filtro suave y dejara la imagen casi igual, el conjunto
tendria casos etiquetados como "captura mala" que se leen perfectamente, y
mediria lo contrario de lo que dice medir.  Por eso cada efecto se
comprueba con una medida y no con la vista.
"""

from datetime import date

import pytest

from app.signals.quality import (
    glare_contrast,
    mean_brightness,
    saturated_fraction,
    sharpness,
)
from app.synthetic.cedula import CedulaData, render_front
from app.synthetic.degradation import (
    blur,
    crop_edge,
    downscale,
    glare,
    jpeg_artifacts,
    occlude,
    rotate,
)


def card():
    return render_front(
        CedulaData(
            nuip="1234567890",
            document_number="000000012",
            surnames="WALTEROS",
            given_names="LAURA",
            birth_date=date(2004, 4, 15),
            birth_place="CARTAGENA (BOLIVAR)",
            sex="F",
            height_m=1.67,
            blood_group="O+",
            issue_date=date(2022, 4, 20),
            issue_place="CARTAGENA",
            expiry_date=date(2032, 4, 19),
        )
    )


# --- El desenfoque -------------------------------------------------------


def test_el_desenfoque_baja_la_nitidez_y_mas_cuanto_mayor_el_radio():
    original = card()
    medidas = [sharpness(blur(original, radius=r)) for r in (1.0, 3.0, 6.0)]

    assert sharpness(original) > medidas[0] > medidas[1] > medidas[2]


def test_bajar_la_resolucion_baja_la_nitidez_sin_cambiar_el_tamano():
    original = card()
    degradada = downscale(original, factor=4.0)

    assert degradada.size == original.size
    assert sharpness(degradada) < sharpness(original) / 2


# --- El reflejo, que el desenfoque no ve ---------------------------------


def test_el_reflejo_y_el_desenfoque_necesitan_medidas_distintas():
    """El punto de tener dos senales de calidad en vez de una.

    Un reflejo del policarbonato deja un campo ilegible sin que la imagen
    este movida, y un movimiento no satura ningun pixel.  Cada medida ve lo
    suyo y ninguna sustituye a la otra: con solo una de las dos, la mitad
    de las capturas malas pasarian por buenas.
    """
    original = card()
    con_reflejo = glare(original, center=(0.55, 0.45), radius=0.25)
    movida = blur(original, radius=6.0)

    # El reflejo satura pixeles; el desenfoque no.
    assert saturated_fraction(con_reflejo) > saturated_fraction(original) + 0.03
    assert saturated_fraction(movida) <= saturated_fraction(original) + 0.005

    # El desenfoque hunde la nitidez; el reflejo la deja mucho mas alta.
    assert sharpness(movida) < sharpness(con_reflejo) / 2


def test_una_compresion_agresiva_no_debe_parecer_un_reflejo():
    """El fallo que destapo la inspeccion visual del panel de degradaciones.

    Con la medida global, un JPEG de calidad 8 daba mas pixeles saturados
    (26,3 %) que un reflejo de verdad (17,4 %): la compresion blanquea el
    fondo entero.  Un caso etiquetado como "comprimido" habria disparado la
    alarma de reflejo y el conjunto mediria lo contrario de lo que dice.
    """
    original = card()
    comprimida = jpeg_artifacts(original, quality=8)
    con_reflejo = glare(original, center=(0.55, 0.45), radius=0.25)

    # La medida global se equivoca: la compresion parece mas reflejo.
    assert saturated_fraction(comprimida) > saturated_fraction(con_reflejo)

    # La medida espacial acierta, y con margen.
    assert glare_contrast(con_reflejo) > 2 * glare_contrast(comprimida)


@pytest.mark.parametrize(
    "centro", [(0.5, 0.5), (0.333, 0.333), (0.25, 0.25), (0.58, 0.42), (0.75, 0.60)]
)
def test_un_reflejo_se_detecta_caiga_donde_caiga(centro):
    """Los centros 0,333 y 0,5 son fronteras exactas de una rejilla de seis.

    Sin bloques solapados, el mismo reflejo puntuaba 0,218 ahi y 0,822 en
    otra posicion: casi cuatro veces menos por un artefacto de la rejilla y
    no por la imagen.  Un caso del conjunto habria pasado o fallado segun
    donde se hubiera puesto el brillo.
    """
    assert glare_contrast(glare(card(), center=centro, radius=0.10)) > 0.5


@pytest.mark.parametrize(
    "degradacion",
    [lambda i: i, lambda i: blur(i, 6.0), lambda i: downscale(i, 5.0)],
)
def test_lo_que_no_es_un_reflejo_no_lo_parece(degradacion):
    assert glare_contrast(degradacion(card())) < 0.3


def test_el_reflejo_aclara_la_imagen():
    original = card()

    assert mean_brightness(glare(original)) > mean_brightness(original)


def test_un_reflejo_de_intensidad_cero_no_cambia_nada():
    """Guarda contra una degradacion que se aplique siempre igual sin mirar
    sus parametros."""
    original = card()

    assert glare(original, intensity=0.0).tobytes() == original.convert("RGB").tobytes()


# --- Tapar y recortar ----------------------------------------------------


def test_tapar_una_region_la_deja_uniforme_y_no_toca_el_resto():
    original = card()
    caja = (0.33, 0.72, 0.70, 0.86)
    tapada = occlude(original, caja)

    region = tapada.crop(
        (
            int(caja[0] * original.width),
            int(caja[1] * original.height),
            int(caja[2] * original.width),
            int(caja[3] * original.height),
        )
    )
    assert len(set(region.convert("L").getdata())) == 1

    # La mitad superior, intacta.
    arriba = (0, 0, original.width, int(0.6 * original.height))
    assert tapada.crop(arriba).tobytes() == original.convert("RGB").crop(arriba).tobytes()


@pytest.mark.parametrize(
    ("side", "eje"),
    [("top", "alto"), ("bottom", "alto"), ("left", "ancho"), ("right", "ancho")],
)
def test_recortar_un_borde_hace_la_imagen_mas_pequena(side, eje):
    original = card()
    recortada = crop_edge(original, side=side, fraction=0.15)

    if eje == "alto":
        assert recortada.height < original.height
        assert recortada.width == original.width
    else:
        assert recortada.width < original.width
        assert recortada.height == original.height


def test_recortar_por_abajo_se_come_la_fecha_de_expiracion():
    """No es lo mismo un campo borroso que un campo ausente: el primero se
    puede intentar leer, el segundo obliga a pedir otra foto."""
    original = card()
    recortada = crop_edge(original, side="bottom", fraction=0.20)

    assert recortada.height == original.height - int(original.height * 0.20)
    # La franja de la fecha de expiracion cae fuera de la imagen recortada.
    assert original.height * 0.83 > recortada.height


def test_un_lado_desconocido_no_se_traga_en_silencio():
    with pytest.raises(ValueError, match="lado desconocido"):
        crop_edge(card(), side="diagonal")


# --- Compresion y giro ---------------------------------------------------


def test_la_compresion_agresiva_deja_huella():
    original = card()
    comprimida = jpeg_artifacts(original, quality=8)

    assert comprimida.size == original.size
    assert comprimida.tobytes() != original.convert("RGB").tobytes()


def test_girar_conserva_el_tamano_y_mueve_el_contenido():
    original = card()
    torcida = rotate(original, degrees=5.0)

    assert torcida.size == original.size
    assert torcida.tobytes() != original.convert("RGB").tobytes()


# --- La medida de nitidez en si ------------------------------------------


def test_una_imagen_plana_no_tiene_nitidez():
    from PIL import Image

    assert sharpness(Image.new("RGB", (40, 40), (200, 200, 200))) == 0.0


def test_una_imagen_diminuta_no_revienta():
    from PIL import Image

    assert sharpness(Image.new("RGB", (2, 2), (10, 200, 30))) == 0.0
