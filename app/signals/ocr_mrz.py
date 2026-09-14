"""Lectura de la MRZ desde la imagen del reverso.

La MRZ es, con diferencia, la parte mas facil de leer de un documento y la
que mas informacion da:

- El alfabeto es minusculo: 26 letras, 10 cifras y el relleno `<`.  Se le
  puede dar a Tesseract la lista cerrada de caracteres posibles, lo que
  elimina de golpe las confusiones con signos de puntuacion.
- El formato es rigido: tres lineas de exactamente 30 caracteres.
- Y, sobre todo, **trae su propia comprobacion**.  Si los digitos de
  control cuadran despues de leerla, la lectura es casi con certeza
  correcta; si no cuadran, puede ser fraude o puede ser que el OCR se
  equivocara, y esas dos cosas hay que mantenerlas separadas (ADR-0003).

Esa ultima propiedad convierte a la MRZ en el unico sitio del documento
donde se puede saber si el OCR acerto **sin tener la respuesta al lado**.
En el anverso no hay forma: si el OCR lee mal un apellido, nada dentro de
la imagen lo delata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pytesseract
from PIL import Image

from app.signals.mrz import TD1_LINE_COUNT, TD1_LINE_LENGTH, MrzData, MrzError, parse_mrz

# Alfabeto cerrado de una MRZ.
#
# Se le pasa a Tesseract como lista blanca, pero conviene saber que **no
# sirve de nada**: con el motor LSTM de Tesseract 5 la salida es
# identica caracter a caracter con y sin `tessedit_char_whitelist`.  Se
# comprobo sobre esta misma MRZ.  La lista se mantiene porque tambien se
# usa para filtrar la salida, que es donde si hace su trabajo.
MRZ_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"

# --psm 6: "un bloque uniforme de texto".  La MRZ son tres renglones
# regulares y tratarla como pagina completa (el modo por defecto) hace que
# Tesseract intente segmentar columnas que no existen.
TESSERACT_CONFIG = f"--psm 6 -c tessedit_char_whitelist={MRZ_ALPHABET}"

# Confusiones tipicas del OCR en una MRZ.  Se aplican SOLO cuando la
# lectura cruda no cuadra, y se prueba una a una: corregir a ciegas
# convertiria una MRZ manipulada en una valida y borraria justo la senal
# que se busca.
CONFUSIONS = (
    # Las dos primeras salieron de leer una MRZ sintetica limpia: el 0 del
    # numero de documento se leyo como Q, y la O de COL como 0.
    ("Q", "0"), ("0", "O"),
    ("O", "0"), ("D", "0"),
    ("I", "1"), ("1", "I"), ("L", "1"),
    ("S", "5"), ("5", "S"),
    ("B", "8"), ("8", "B"),
    ("Z", "2"), ("2", "Z"),
    ("G", "6"), ("6", "G"),
)


# Cifras que el OCR pone donde tiene que haber una letra.  Se usan solo en
# los campos que son alfabeticos por definicion.
DIGIT_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}

# Campos de la MRZ que **ningun digito de control cubre**: el pais emisor,
# el sexo, la nacionalidad y la linea entera de nombres quedan fuera del
# payload de los cuatro checks.
#
# Eso los convierte en el punto ciego del formato, y medirlo lo dejo claro:
# sobre 70 lecturas de MRZ sinteticas, 53 tenian los cuatro digitos
# cuadrados y 52 de esas 53 estaban mal leidas.  Casi siempre por lo mismo,
# un "COL" leido "C0L" que la aritmetica no puede ver.
#
# Corregirlos es seguro precisamente porque no participan en ningun check:
# una correccion aqui no puede fabricar una validez aritmetica que no
# existiera.  Eso no valdria para el numero de documento o las fechas, y por
# eso esas se dejan en manos de la reparacion timida de _repair().
ALPHA_ONLY_SPANS = (
    (0, 2, 5),    # linea 1: pais emisor
    (1, 15, 18),  # linea 2: nacionalidad
    (2, 0, 30),   # linea 3: apellidos y nombres
)
VALID_SEX = frozenset("MF<")

# El camino inverso: letras donde el formato exige cifras.
LETTER_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                   "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7"}

# Campos estrictamente numericos.
#
# Las fechas lo son por ICAO y los digitos de control tambien.  El numero de
# documento es numerico **en la cedula colombiana**; en otros paises puede
# llevar letras, asi que esto es una suposicion del dominio de este
# proyecto y no del formato.
#
# Corregir aqui si puede hacer cuadrar un digito que no cuadraba, lo que
# obliga a justificarlo: una letra en una fecha es siempre un fallo de
# lectura y nunca una manipulacion, porque quien edita una MRZ para
# falsificar pone cifras.  No hay fraude que se parezca a esto.
NUMERIC_SPANS = (
    (0, 5, 15),   # linea 1: numero de documento y su digito
    (1, 0, 7),    # linea 2: fecha de nacimiento y su digito
    (1, 8, 15),   # linea 2: fecha de expiracion y su digito
    (1, 29, 30),  # linea 2: digito compuesto
)


def _fix_alpha_fields(lines: list[str]) -> list[str]:
    """Pone letras donde el formato exige letras.

    No adivina cual letra: aplica la confusion inversa conocida del OCR
    (0 por O, 1 por I, 5 por S...), que es determinista.
    """
    fixed = list(lines)
    for row, start, end in ALPHA_ONLY_SPANS:
        if row >= len(fixed):
            continue
        line = fixed[row]
        span = "".join(DIGIT_TO_LETTER.get(c, c) for c in line[start:end])
        fixed[row] = line[:start] + span + line[end:]

    for row, start, end in NUMERIC_SPANS:
        if row >= len(fixed):
            continue
        line = fixed[row]
        span = "".join(LETTER_TO_DIGIT.get(c, c) for c in line[start:end])
        fixed[row] = line[:start] + span + line[end:]

    # El pais emisor y la nacionalidad son, en este proyecto, siempre COL.
    # Es una suposicion del dominio: la herramienta solo procesa cedulas
    # colombianas.  Se corrige solo si esta a un caracter de distancia, para
    # que un documento de otro pais salga mal leido en vez de convertido en
    # colombiano sin avisar.
    for row, start in ((0, 2), (1, 15)):
        if row >= len(fixed):
            continue
        code = fixed[row][start : start + 3]
        if code != "COL" and sum(a != b for a, b in zip(code, "COL")) == 1:
            fixed[row] = fixed[row][:start] + "COL" + fixed[row][start + 3 :]

    if len(fixed) > 1 and len(fixed[1]) > 7 and fixed[1][7] not in VALID_SEX:
        # El sexo tampoco esta cubierto por ningun digito.  Solo se corrige
        # si la cifra leida tiene una letra confundible detras y esa letra
        # es un sexo valido; si no, se deja como esta para que se vea.
        candidate = DIGIT_TO_LETTER.get(fixed[1][7])
        if candidate in VALID_SEX:
            fixed[1] = fixed[1][:7] + candidate + fixed[1][8:]

    return fixed


@dataclass(frozen=True)
class MrzReading:
    lines: list[str]
    raw_text: str
    repaired: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def parsed(self) -> MrzData | None:
        if not self.ok:
            return None
        try:
            return parse_mrz(self.lines)
        except MrzError:
            return None


def crop_mrz_band(image: Image.Image, top: float = 0.60) -> Image.Image:
    """La franja inferior del reverso, donde vive la MRZ.

    Es un recorte por proporcion y no una deteccion: vale para documentos
    ya rectificados, que es lo que produce el generador sintetico.  Con una
    foto real torcida hara falta detectar y enderezar el documento antes, y
    eso todavia no existe.
    """
    return image.crop((0, int(image.height * top), image.width, image.height))


def binarize(image: Image.Image, bias: float = 0.30) -> Image.Image:
    """Binarizado por umbral global.

    **No se usa en el camino normal, y esta aqui documentando por que.**

    Parecia el preprocesado obvio para una MRZ y resulto ser
    contraproducente: sobre la banda en escala de grises Tesseract leia las
    tres lineas, y sobre esta misma banda binarizada devolvia una cadena
    vacia.  El motor ya aplica su propio umbral, y darle una imagen de dos
    niveles le quita la informacion que usa para hacerlo bien.

    Se conserva porque con fotos reales mal iluminadas puede volver a hacer
    falta algun preprocesado, y entonces conviene tener anotado que este en
    concreto empeora las capturas limpias.
    """
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    threshold = gray.mean() - bias * gray.std()
    return Image.fromarray(np.where(gray < threshold, 0, 255).astype(np.uint8))


def _candidate_lines(text: str) -> list[str]:
    """Saca de la salida de Tesseract las lineas con pinta de MRZ."""
    lines = []
    for raw in text.splitlines():
        cleaned = re.sub(rf"[^{MRZ_ALPHABET}]", "", raw.upper())
        if len(cleaned) >= TD1_LINE_LENGTH - 6:
            lines.append(cleaned)
    return lines


def _pad_or_trim(line: str) -> str:
    if len(line) < TD1_LINE_LENGTH:
        return line + "<" * (TD1_LINE_LENGTH - len(line))
    return line[:TD1_LINE_LENGTH]


def _checks_pass(lines: list[str]) -> bool:
    try:
        return parse_mrz(lines).checks_ok
    except MrzError:
        return False


def _repair(lines: list[str]) -> list[str] | None:
    """Prueba una sola sustitucion de caracter confundible por linea.

    La reparacion es deliberadamente timida.  Un corrector agresivo
    encontraria, para casi cualquier MRZ rota, alguna combinacion de
    cambios que hace cuadrar los digitos, y entonces un documento
    manipulado saldria limpio: el corrector habria fabricado la validez que
    debia comprobar.

    Aqui solo se acepta una correccion si con **un unico** caracter
    cambiado los cuatro digitos de control cuadran.  Que cuadren los cuatro
    despues de tocar un caracter es muy improbable por azar, asi que esa
    correccion casi seguro es la lectura verdadera.
    """
    for index, line in enumerate(lines):
        for position, character in enumerate(line):
            for wrong, right in CONFUSIONS:
                if character != wrong:
                    continue
                candidate = list(lines)
                candidate[index] = line[:position] + right + line[position + 1 :]
                if _checks_pass(candidate):
                    return candidate
    return None


def read_mrz(image: Image.Image, *, repair: bool = True) -> MrzReading:
    # Se le da la banda en escala de grises, sin binarizar: ver binarize().
    band = crop_mrz_band(image).convert("L")
    text = pytesseract.image_to_string(band, config=TESSERACT_CONFIG)
    lines = _candidate_lines(text)

    if len(lines) < TD1_LINE_COUNT:
        return MrzReading(
            lines=lines,
            raw_text=text,
            repaired=False,
            error=(
                f"solo se reconocieron {len(lines)} lineas con forma de MRZ, "
                f"hacen falta {TD1_LINE_COUNT}"
            ),
        )

    lines = [_pad_or_trim(line) for line in lines[-TD1_LINE_COUNT:]]
    lines = _fix_alpha_fields(lines)

    if _checks_pass(lines):
        return MrzReading(lines=lines, raw_text=text, repaired=False)

    if repair:
        corrected = _repair(lines)
        if corrected is not None:
            return MrzReading(lines=corrected, raw_text=text, repaired=True)

    # Se devuelve la lectura tal cual, sin error: que los digitos no cuadren
    # NO es un fallo de lectura, es justo lo que hay que reportar como senal.
    return MrzReading(lines=lines, raw_text=text, repaired=False)
