"""Generador de cedulas colombianas sinteticas.

Produce anverso y reverso de una cedula de ciudadania de policarbonato,
con la misma disposicion de campos que el documento real y con una MRZ
TD1 **coherente con lo impreso en el anverso**.

Que la MRZ sea coherente no es un detalle estetico. El conjunto de
evaluacion se etiqueta a mano con la decision correcta de cada caso, y un
documento "legitimo" cuya MRZ no cuadrara seria un caso de fraude sin
querer: el sistema lo marcaria, con razon, y la etiqueta diria que estaba
bien.  El conjunto entero quedaria mal desde el principio.

El layout se tomo del especimen publico de la Registraduria y de una
cedula real.  Ningun dato real aparece aqui.

Limitaciones conocidas, que importan al interpretar cualquier medida
hecha sobre estas imagenes:

- La MRZ real esta impresa en OCR-B y aqui se usa DejaVu Sans Mono. Un
  lector entrenado en OCR-B puede comportarse distinto.
- No hay holograma, ni fondo de seguridad real, ni relieve, ni tinta
  cambiante.  Cualquier deteccion de falsificacion que dependa de esos
  elementos no se puede evaluar con este generador.
- El retrato se inserta desde fuera; si no se da ninguno, se dibuja un
  marcador que no es una cara y que ningun detector facial encontrara.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.signals.mrz import build_td1

# ID-1 (85,6 x 54 mm) a 300 ppp.  Se genera a la resolucion de un escaner
# de documentos para que reducirla luego sea una degradacion deliberada y
# medible, en vez de un limite del generador.
WIDTH, HEIGHT = 1012, 638

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_REGULAR = FONT_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_MONO = FONT_DIR / "DejaVuSansMono.ttf"

PAPER = (244, 248, 251)
GUILLOCHE = (206, 223, 238)
RED = (200, 16, 46)
LABEL = (110, 122, 138)
VALUE = (17, 24, 39)
FLAG_YELLOW = (252, 209, 22)
FLAG_BLUE = (0, 56, 147)
FLAG_RED = (206, 17, 38)

MONTHS_ES = (
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
)


def format_date_es(value: date) -> str:
    """Como lo imprime la cedula: '14 ENE 1998'."""
    return f"{value.day:02d} {MONTHS_ES[value.month - 1]} {value.year}"


def format_nuip(nuip: str) -> str:
    """Con puntos de millar, como en el anverso: '1.020.483.236'.

    En la MRZ el mismo numero viaja sin puntos, asi que cotejar las dos
    copias exige normalizar.  Esa asimetria es del documento, no del
    codigo, y conviene que el generador la reproduzca: si el generador
    imprimiera el NUIP sin puntos, el cotejo pareceria funcionar en las
    pruebas y fallaria con documentos de verdad.
    """
    digits = "".join(character for character in nuip if character.isdigit())
    groups: list[str] = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    if digits:
        groups.insert(0, digits)
    return ".".join(groups)


@dataclass(frozen=True)
class CedulaData:
    nuip: str
    document_number: str
    surnames: str
    given_names: str
    birth_date: date
    birth_place: str
    sex: str
    height_m: float
    blood_group: str
    issue_date: date
    issue_place: str
    expiry_date: date
    nationality: str = "COL"

    def mrz(self) -> list[str]:
        return build_td1(
            document_number=self.document_number,
            birth_date=self.birth_date,
            sex=self.sex,
            expiry_date=self.expiry_date,
            identity_number=self.nuip,
            surnames=self.surnames,
            given_names=self.given_names,
            nationality=self.nationality,
        )

    def with_changes(self, **changes) -> CedulaData:
        return replace(self, **changes)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _card(draw: ImageDraw.ImageDraw) -> None:
    """Fondo con un guilloche suave.

    No imita ninguna medida de seguridad real; esta para que el OCR no
    trabaje sobre un blanco perfecto, que es una facilidad que el
    documento de verdad no le da.
    """
    draw.rounded_rectangle((0, 0, WIDTH - 1, HEIGHT - 1), radius=28, fill=PAPER)
    for offset in range(0, WIDTH, 14):
        points = [
            (offset + amplitude, y)
            for y in range(0, HEIGHT, 8)
            for amplitude in (int(16 * math.sin(y / 46.0 + offset / 90.0)),)
        ]
        draw.line(points, fill=GUILLOCHE, width=1)


def _flag(draw: ImageDraw.ImageDraw, x: int, y: int, width: int = 58) -> None:
    height = int(width * 0.66)
    draw.rectangle((x, y, x + width, y + height // 2), fill=FLAG_YELLOW)
    draw.rectangle((x, y + height // 2, x + width, y + int(height * 0.75)), fill=FLAG_BLUE)
    draw.rectangle((x, y + int(height * 0.75), x + width, y + height), fill=FLAG_RED)


def _field(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    value: str,
    label_font: ImageFont.FreeTypeFont,
    value_font: ImageFont.FreeTypeFont,
) -> None:
    draw.text((x, y), label, font=label_font, fill=LABEL)
    draw.text((x, y + 20), value, font=value_font, fill=VALUE)


def _butterfly(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """La mariposa multicolor de la esquina inferior derecha.

    Es un elemento grafico del documento, no una medida de seguridad que
    aqui se pueda reproducir.  Se dibuja porque ocupa sitio: sin ella, esa
    esquina queda vacia y un recorte mal hecho pasaria desapercibido.
    """
    wings = (
        ((x, y + 14), (x + 26, y - 10), (x + 34, y + 14), FLAG_YELLOW),
        ((x + 34, y + 14), (x + 60, y - 6), (x + 64, y + 18), (0, 140, 180)),
        ((x + 2, y + 16), (x + 24, y + 40), (x + 34, y + 18), FLAG_RED),
        ((x + 34, y + 18), (x + 58, y + 38), (x + 64, y + 20), (120, 70, 160)),
    )
    for *points, color in wings:
        draw.polygon(points, fill=color)
    draw.line((x + 33, y - 4, x + 33, y + 38), fill=(40, 40, 40), width=3)


def _placeholder_portrait(size: tuple[int, int]) -> Image.Image:
    """Marcador para cuando no se aporta retrato.

    A proposito NO parece una cara: si lo pareciera a medias, un detector
    facial podria encontrarla a veces, y el conjunto de evaluacion tendria
    una similitud facial que no significa nada.  Mejor que no encuentre
    nada y la senal salga como no disponible, que es la verdad.
    """
    portrait = Image.new("RGB", size, (214, 222, 230))
    draw = ImageDraw.Draw(portrait)
    for step in range(0, size[0] + size[1], 18):
        draw.line((step, 0, 0, step), fill=(196, 206, 217), width=2)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(170, 182, 196), width=3)
    return portrait


def _fit_portrait(photo: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Recorta al centro y escala, sin deformar la cara.

    Un `resize` directo al hueco del retrato estira o aplasta la foto segun
    su proporcion original, y una cara deformada produce un embedding
    distinto del de la misma cara sin deformar.  Eso haria que la similitud
    bajara por un defecto del generador y no por nada del documento, que es
    la peor clase de error: uno que se mide y se atribuye a otra cosa.
    """
    ancho, alto = size
    proporcion_destino = ancho / alto
    proporcion_origen = photo.width / photo.height

    if proporcion_origen > proporcion_destino:
        # La foto es mas ancha de lo que cabe: se recorta a los lados.
        nuevo_ancho = int(photo.height * proporcion_destino)
        izquierda = (photo.width - nuevo_ancho) // 2
        recorte = photo.crop((izquierda, 0, izquierda + nuevo_ancho, photo.height))
    else:
        # Mas alta: se recorta arriba y abajo. Se deja mas margen abajo que
        # arriba porque en un retrato la cara vive en el tercio superior, y
        # recortar por el centro geometrico le corta la frente.
        nuevo_alto = int(photo.width / proporcion_destino)
        arriba = (photo.height - nuevo_alto) // 3
        recorte = photo.crop((0, arriba, photo.width, arriba + nuevo_alto))

    return recorte.resize(size, Image.LANCZOS)


def render_front(data: CedulaData, portrait: Image.Image | None = None) -> Image.Image:
    card = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(card)
    _card(draw)

    tiny = font(FONT_REGULAR, 15)
    label = font(FONT_REGULAR, 16)
    value = font(FONT_BOLD, 26)
    value_small = font(FONT_BOLD, 24)

    # La bandera va en el hueco entre el rotulo y el titulo.  A 108 px
    # pisaba la palabra "CIUDADANÍA" y a 152 px pisaba la R de REPÚBLICA;
    # en ambos casos el OCR habria leido texto mutilado, que es un defecto
    # del generador disfrazado de documento dificil.
    _flag(draw, 130, 40, width=50)
    draw.text((36, 42), "CÉDULA DE", font=tiny, fill=(70, 86, 108))
    draw.text((36, 60), "CIUDADANÍA", font=tiny, fill=(70, 86, 108))
    draw.text((196, 32), "REPÚBLICA DE COLOMBIA", font=font(FONT_BOLD, 40), fill=RED)
    draw.text(
        (614, 88), f"NUIP {format_nuip(data.nuip)}", font=font(FONT_BOLD, 25), fill=VALUE
    )

    photo_box = (36, 126, 292, 500)
    hueco = (photo_box[2] - photo_box[0], photo_box[3] - photo_box[1])
    photo = (
        _fit_portrait(portrait.convert("RGB"), hueco)
        if portrait is not None
        else _placeholder_portrait(hueco)
    )
    card.paste(photo, photo_box[:2])

    left = 330
    _field(draw, left, 126, "Apellidos", data.surnames, label, value)
    _field(draw, left, 196, "Nombres", data.given_names, label, value)

    _field(draw, left, 266, "Nacionalidad", data.nationality, label, value_small)
    _field(draw, left + 232, 266, "Estatura", f"{data.height_m:.2f}", label, value_small)
    _field(draw, left + 392, 266, "Sexo", data.sex, label, value_small)

    _field(draw, left, 336, "Fecha de nacimiento",
           format_date_es(data.birth_date), label, value_small)
    _field(draw, left + 232, 336, "G.S.", data.blood_group, label, value_small)

    _field(draw, left, 406, "Lugar de nacimiento", data.birth_place, label, value_small)
    _field(draw, left, 468, "Fecha y lugar de expedición",
           f"{format_date_es(data.issue_date)}, {data.issue_place}", label, value_small)
    _field(draw, left, 530, "Fecha de expiración",
           format_date_es(data.expiry_date), label, value_small)

    # Retrato fantasma: el mismo rostro repetido en pequeno y desvaido.
    # Se genera desde el retrato real cuando lo hay, porque una manipulacion
    # que cambie la foto principal y no el fantasma es un caso de fraude
    # que el conjunto de evaluacion deberia poder construir.
    ghost = photo.resize((92, 118))
    card.paste(Image.blend(ghost, Image.new("RGB", (92, 118), PAPER), 0.62), (886, 150))

    _butterfly(draw, 890, 520)

    draw.text((36, 512), "Firma", font=tiny, fill=LABEL)
    draw.text((44, 532), f"{data.given_names.split()[0].title()} {data.surnames.split()[0].title()}",
              font=font(FONT_REGULAR, 30), fill=(24, 38, 74))

    return card


def render_back(data: CedulaData, mrz_lines: list[str] | None = None) -> Image.Image:
    card = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(card)
    _card(draw)

    lines = mrz_lines if mrz_lines is not None else data.mrz()

    draw.text((40, 34), ".CO", font=font(FONT_BOLD, 20), fill=(70, 86, 108))

    serial = Image.new("RGB", (220, 26), PAPER)
    ImageDraw.Draw(serial).text(
        (0, 0), data.document_number, font=font(FONT_MONO, 22), fill=(70, 86, 108)
    )
    card.paste(serial.rotate(90, expand=True), (34, 80))

    # Marcador del codigo de barras bidimensional.  No es un QR valido y no
    # pretende serlo: el QR de la cedula real lleva datos firmados por la
    # Registraduria y sin su clave publica no se puede validar nada, asi
    # que generarlo solo daria una falsa sensacion de realismo.
    block = 14
    for row in range(14):
        for column in range(14):
            if (row * 7 + column * 5 + (row * column) % 3) % 3:
                draw.rectangle(
                    (700 + column * block, 60 + row * block,
                     700 + column * block + block - 2, 60 + row * block + block - 2),
                    fill=(28, 36, 52),
                )

    draw.text((300, 300), "REGISTRADOR NACIONAL", font=font(FONT_REGULAR, 17), fill=LABEL)

    mono = font(FONT_MONO, 33)
    for index, line in enumerate(lines):
        draw.text((60, 420 + index * 48), line, font=mono, fill=(12, 16, 26))

    return card
