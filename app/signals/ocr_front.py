"""Lectura de los campos impresos en el anverso.

A diferencia de la MRZ, aqui **no hay forma de saber si el OCR acerto**.
Si lee mal un apellido, nada dentro de la imagen lo delata: no hay digitos
de control ni redundancia. Lo unico que se puede hacer es entregar cada
campo con la confianza que el motor le asigna, y dejar que el cotejo con
la MRZ y el agente decidan cuanto fiarse.

La extraccion se ancla en las **etiquetas impresas** ("Apellidos",
"Nombres", "Fecha de nacimiento"...) y no en coordenadas fijas. Anclar en
coordenadas habria sido mas facil, porque el generador sintetico siempre
pone los campos en el mismo sitio, y habria producido un lector que
funciona de maravilla con las imagenes de casa y se rompe con la primera
foto de tamano distinto. Las etiquetas estan impresas en el documento y se
mueven con el.

Dos detalles de la salida de Tesseract que dan forma a este modulo:

- Su agrupacion en bloques y parrafos **no es fiable** en este layout: una
  misma "linea" suya mezcla la palabra "Firma" de la esquina inferior
  izquierda con "Fecha de expiracion" del centro. Las filas se reconstruyen
  aqui por coordenada vertical.
- Los valores viven en la fila siguiente a su etiqueta y con el mismo
  margen izquierdo, salvo el NUIP, que va en la misma fila.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import pytesseract
from PIL import Image
from pytesseract import Output

from app.synthetic.cedula import MONTHS_ES

# Alto tipico de una fila en la imagen a 300 ppp.  Se usa para decidir que
# dos palabras estan en el mismo renglon y para buscar el valor debajo de
# su etiqueta.
ROW_TOLERANCE = 14
VALUE_GAP = 60


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


@dataclass(frozen=True)
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def middle(self) -> int:
        return self.top + self.height // 2


@dataclass(frozen=True)
class Field:
    value: str
    confidence: float
    # De donde salio el campo: "label" si se localizo por su rotulo impreso,
    # "order" si hubo que deducirlo por el orden de los campos.  Viaja hasta
    # el agente a proposito: una fecha deducida por su posicion en la lista
    # merece menos credito que una leida debajo de "Fecha de nacimiento", y
    # esa diferencia no puede quedarse dentro del extractor.
    source: str = "label"

    def __bool__(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class FrontFields:
    fields: dict[str, Field]

    def get(self, name: str) -> Field | None:
        return self.fields.get(name)

    def value(self, name: str) -> str | None:
        field = self.fields.get(name)
        return field.value if field else None

    def confidence(self, name: str) -> float | None:
        field = self.fields.get(name)
        return field.confidence if field else None


# Etiqueta impresa -> nombre del campo.  El orden importa: se prueba la
# coincidencia mas larga primero, para que "fecha de nacimiento" no la gane
# "fecha" y "lugar de nacimiento" no se confunda con "fecha de nacimiento".
LABELS: tuple[tuple[str, str], ...] = (
    ("fecha y lugar de expedicion", "issue"),
    ("fecha de nacimiento", "birth_date"),
    ("lugar de nacimiento", "birth_place"),
    ("fecha de expiracion", "expiry_date"),
    ("nacionalidad", "nationality"),
    ("apellidos", "surnames"),
    ("estatura", "height"),
    ("nombres", "given_names"),
    ("sexo", "sex"),
    ("nuip", "nuip"),
    ("gs", "blood_group"),
)


def words_of(image: Image.Image, lang: str = "spa") -> list[Word]:
    data = pytesseract.image_to_data(
        image.convert("L"), lang=lang, output_type=Output.DICT
    )
    words = []
    for index, text in enumerate(data["text"]):
        cleaned = text.strip()
        if not cleaned:
            continue
        confidence = float(data["conf"][index])
        if confidence < 0:
            continue
        words.append(
            Word(
                text=cleaned,
                conf=confidence,
                left=int(data["left"][index]),
                top=int(data["top"][index]),
                width=int(data["width"][index]),
                height=int(data["height"][index]),
            )
        )
    return words


def row_middle(row: list[Word]) -> int:
    """Altura representativa de una fila: la mediana de sus palabras.

    No vale usar la primera palabra por la izquierda.  En este documento la
    firma manuscrita cae a la altura del rotulo "Fecha de expiracion" pero
    con otra caja, y tomando su centro como el de la fila entera la fila de
    la etiqueta parecia estar *debajo* de si misma: el extractor devolvia el
    rotulo como si fuera el valor.
    """
    middles = sorted(w.middle for w in row)
    return middles[len(middles) // 2]


def rows_of(words: list[Word]) -> list[list[Word]]:
    """Reconstruye los renglones por coordenada vertical.

    No se usa la numeracion de lineas de Tesseract porque en este layout
    agrupa cosas que estan a media cedula de distancia.
    """
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.middle, w.left)):
        for row in rows:
            if abs(row[0].middle - word.middle) <= ROW_TOLERANCE:
                row.append(word)
                break
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w.left)
    return rows


def _find_labels(rows: list[list[Word]]) -> list[tuple[str, list[Word], list[Word]]]:
    """Localiza cada etiqueta y devuelve (campo, palabras de la etiqueta, fila)."""
    found: list[tuple[str, list[Word], list[Word]]] = []
    taken: set[int] = set()

    for label, name in LABELS:
        target = label.split()
        for row in rows:
            for start in range(len(row)):
                span = row[start : start + len(target)]
                if len(span) < len(target):
                    continue
                if any(id(w) in taken for w in span):
                    continue
                if [normalize(w.text) for w in span] == target:
                    found.append((name, list(span), row))
                    taken.update(id(w) for w in span)
                    break
            else:
                continue
            break
    return found


def _right_limit(label_words: list[Word], row: list[Word], page_width: int) -> int:
    """Hasta donde llega la columna de un campo.

    Si en la misma fila hay otra etiqueta a la derecha, la columna termina
    donde empieza esa.  Asi "Nacionalidad", "Estatura" y "Sexo" se reparten
    solos el ancho sin tener que escribir sus coordenadas a mano.
    """
    label_right = label_words[-1].right
    following = [w.left for w in row if w.left > label_right + 20]
    return min(following) - 10 if following else page_width


DATE_PATTERN = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})\b")
NUIP_PATTERN = re.compile(r"\b\d{1,3}(?:[.\s]\d{3}){2,3}\b")

# Orden vertical de los campos en la cedula de ciudadania.  Es una
# propiedad del documento y no del generador: la Registraduria imprime
# siempre apellidos, nombres, y despues nacimiento, expedicion y
# expiracion en ese orden.
ORDERED_DATES = ("birth_date", "issue", "expiry_date")


def _text_of(row: list[Word]) -> str:
    return " ".join(w.text for w in row)


def _confidence_of(row: list[Word]) -> float:
    return sum(w.conf for w in row) / len(row) / 100.0


def _fallback_by_order(rows: list[list[Word]], have: set[str]) -> dict[str, Field]:
    """Rescata campos cuando sus rotulos no se han podido leer.

    Es el caso que destapo la medicion: con un desenfoque leve, los rotulos
    -- gris claro y cuerpo pequeno -- desaparecen mientras los valores, en
    negrita y mas grandes, siguen leyendose con confianza de 90 y pico.  Sin
    este rescate el extractor devolvia un unico campo de once, y la
    conclusion facil habria sido "el anverso no se puede leer", que es
    falsa.

    El anclaje es el **orden vertical**, que es fijo en la cedula
    colombiana, y no las coordenadas, que cambian con el encuadre.  Aun asi
    es menos fiable que un rotulo, y por eso cada campo asi obtenido se
    marca con source="order".
    """
    rescued: dict[str, Field] = {}
    ordered = sorted(rows, key=row_middle)

    dated = [row for row in ordered if DATE_PATTERN.search(_text_of(row))]
    for name, row in zip(ORDERED_DATES, dated):
        if name not in have:
            rescued[name] = Field(_text_of(row), _confidence_of(row), source="order")

    if "nuip" not in have:
        for row in ordered:
            match = NUIP_PATTERN.search(_text_of(row))
            if match:
                rescued["nuip"] = Field(
                    match.group(0), _confidence_of(row), source="order"
                )
                break

    # Apellidos y nombres son las dos primeras filas en mayusculas que
    # aparecen por debajo del NUIP.  "REPUBLICA DE COLOMBIA" queda por
    # encima y la nacionalidad ("COL") viene despues de las dos.
    nuip_height = next(
        (row_middle(row) for row in ordered if NUIP_PATTERN.search(_text_of(row))),
        None,
    )
    if nuip_height is not None:
        uppercase = [
            row
            for row in ordered
            if row_middle(row) > nuip_height
            and all(w.text.isupper() and w.text.isalpha() for w in row)
        ]
        for name, row in zip(("surnames", "given_names"), uppercase):
            if name not in have:
                rescued[name] = Field(
                    _text_of(row), _confidence_of(row), source="order"
                )

    return rescued


def read_front(image: Image.Image, lang: str = "spa") -> FrontFields:
    words = words_of(image, lang=lang)
    rows = rows_of(words)
    fields: dict[str, Field] = {}

    for name, label_words, row in _find_labels(rows):
        left = label_words[0].left
        right = _right_limit(label_words, row, image.width)
        bottom = max(w.top + w.height for w in label_words)

        if name == "nuip":
            # El unico campo cuyo valor va en la misma fila que su rotulo.
            value_words = [w for w in row if w.left > label_words[-1].right]
        else:
            label_ids = {id(w) for w in label_words}
            candidates = sorted(
                (
                    candidate
                    for candidate in rows
                    if candidate is not row
                    and bottom < row_middle(candidate) < bottom + VALUE_GAP
                ),
                key=row_middle,
            )
            value_words = []
            for candidate in candidates:
                inside = [
                    w
                    for w in candidate
                    if left - 15 <= w.left
                    and w.right <= right + 15
                    and id(w) not in label_ids
                ]
                if inside:
                    value_words = inside
                    break

        if not value_words:
            continue

        fields[name] = Field(
            value=" ".join(w.text for w in value_words),
            confidence=sum(w.conf for w in value_words) / len(value_words) / 100.0,
        )

    fields.update(_fallback_by_order(rows, set(fields)))
    return FrontFields(fields=fields)


# --- Interpretacion de los valores leidos --------------------------------


def parse_nuip(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    return digits or None


def parse_spanish_date(raw: str) -> date | None:
    """'14 ENE 1998' -> date(1998, 1, 14).

    Devuelve None en vez de lanzar: una fecha que el OCR no supo leer es
    informacion para el agente, no un error del programa.
    """
    match = re.search(r"(\d{1,2})\s*([A-Za-z]{3})\w*\s*(\d{4})", raw)
    if not match:
        return None

    day, month_name, year = match.groups()
    month_key = normalize(month_name).upper()[:3]
    if month_key not in MONTHS_ES:
        return None

    try:
        return date(int(year), MONTHS_ES.index(month_key) + 1, int(day))
    except ValueError:
        return None


def parse_place_and_date(raw: str) -> tuple[date | None, str | None]:
    """'21 ENE 2016, BELLO' -> (date(2016, 1, 21), 'BELLO')."""
    parsed = parse_spanish_date(raw)
    place = raw.split(",", 1)[1].strip() if "," in raw else None
    return parsed, place or None
