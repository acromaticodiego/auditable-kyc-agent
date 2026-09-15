"""Pruebas del lector de rostros.

Lo que NO se puede comprobar aqui es si ArcFace distingue a una persona de
otra: para eso hacen falta caras reales, que viven fuera del repositorio
(ver data/real/caras/LEEME.md). Lo que si se fija es el contrato, que es
donde estan los errores que no dan error: una lectura que se inventa un
valor cuando no hay cara, o una similitud que sale 0.0 cuando lo cierto es
que no se ha podido mirar.
"""

from datetime import date

import numpy as np
import pytest
from PIL import Image

from app.signals.face import FaceReader, FaceReading, similarity
from app.synthetic.cedula import CedulaData, render_front


@pytest.fixture(scope="module")
def lector() -> FaceReader:
    return FaceReader()


def persona() -> CedulaData:
    return CedulaData(
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


def test_una_lectura_tiene_embedding_o_motivo_pero_nunca_las_dos():
    """Una lectura con las dos cosas seria ambigua para quien la use.

    Y una sin ninguna es un objeto que no dice nada. Mejor que reviente al
    construirla que descubrirlo tres capas mas arriba.
    """
    with pytest.raises(ValueError):
        FaceReading(embedding=np.zeros(512, dtype=np.float32), reason="algo")
    with pytest.raises(ValueError):
        FaceReading()


def test_la_similitud_sin_una_de_las_dos_caras_es_none_y_no_cero():
    """Un cero es una afirmacion: 'estas caras no se parecen en nada'.

    Cuando falta una lectura no hay ninguna afirmacion que hacer, y devolver
    cero la haria. En un KYC ese cero se leeria como que la selfie no es de
    la persona del documento, que es acusar a alguien por una foto que nadie
    pudo procesar.
    """
    sin_cara = FaceReading(reason="no se detecto ninguna cara en la imagen")
    con_cara = FaceReading(embedding=np.ones(512, dtype=np.float32) / np.sqrt(512))

    assert similarity(sin_cara, con_cara) is None
    assert similarity(con_cara, sin_cara) is None
    assert similarity(sin_cara, sin_cara) is None


def test_la_similitud_de_dos_embeddings_es_su_producto_escalar():
    """Los embeddings vienen normalizados, asi que el escalar ES el coseno."""
    uno = np.zeros(512, dtype=np.float32)
    uno[0] = 1.0
    otro = np.zeros(512, dtype=np.float32)
    otro[0] = 1.0

    assert similarity(FaceReading(embedding=uno), FaceReading(embedding=otro)) == 1.0

    ortogonal = np.zeros(512, dtype=np.float32)
    ortogonal[1] = 1.0
    assert similarity(
        FaceReading(embedding=uno), FaceReading(embedding=ortogonal)
    ) == 0.0


def test_el_modelo_no_se_carga_al_construir_el_lector():
    """Son 200 MB. Cargarlos al importar los cobraria a quien no usa caras."""
    nuevo = FaceReader()

    assert nuevo._model is None


def test_el_retrato_de_la_cedula_sintetica_no_es_una_cara(lector):
    """Deja escrito por que la senal facial todavia no se puede medir.

    El retrato del generador es un rectangulo gris, un marcador de posicion.
    El detector no encuentra nada en el, asi que conectar ArcFace sin caras
    reales daria una senal que sale siempre no disponible. Este test es la
    prueba de esa afirmacion, no una suposicion.
    """
    lectura = lector.read(render_front(persona()))

    assert not lectura.available
    assert lectura.faces_found == 0
    assert "ninguna cara" in lectura.reason


def test_una_imagen_lisa_no_produce_ninguna_cara(lector):
    lectura = lector.read(Image.new("RGB", (640, 480), (200, 200, 200)))

    assert not lectura.available
    assert lectura.embedding is None


def test_un_fichero_que_no_es_imagen_da_el_motivo_y_no_revienta(lector, tmp_path):
    ruta = tmp_path / "esto-no-es-una-foto.jpg"
    ruta.write_bytes(b"ni de lejos")

    lectura = lector.read(ruta)

    assert not lectura.available
    assert "no se pudo abrir" in lectura.reason
