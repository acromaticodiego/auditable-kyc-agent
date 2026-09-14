"""Lectura y verificacion de la zona legible por maquina (MRZ).

El reverso de la cedula colombiana de policarbonato lleva una MRZ en
formato TD1 de ICAO 9303: tres lineas de 30 caracteres con **digitos de
control** calculados sobre los propios datos.

Esto es lo mas valioso que tiene el documento para este proyecto, y no
hace falta ningun modelo para aprovecharlo:

- Los digitos de control son aritmetica pura.  Quien retoque el numero o
  una fecha en el anverso y no recalcule la MRZ deja una inconsistencia
  detectable con certeza, no con probabilidad.
- El numero de identidad y las fechas aparecen **dos veces** en el
  documento, en el anverso impreso y dentro de la MRZ.  Cotejar las dos
  copias convierte una manipulacion del anverso en una senal dura.

Ninguna de las dos cosas prueba que el documento sea autentico: una MRZ
inventada desde cero es perfectamente consistente consigo misma.  Lo que
detectan es la manipulacion de un documento real, que es el fraude
frecuente.  Ver docs/adr/0003-la-mrz-como-senal-dura.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

TD1_LINE_LENGTH = 30
TD1_LINE_COUNT = 3

# Pesos ciclicos del algoritmo de ICAO 9303.
_WEIGHTS = (7, 3, 1)

_VALID_CHARACTERS = re.compile(r"^[A-Z0-9<]+$")


class MrzError(ValueError):
    """La MRZ no tiene la forma de una MRZ.

    Se distingue de un digito de control que no cuadra: lo primero es una
    lectura fallida (probablemente del OCR), lo segundo es una senal sobre
    el documento.  Confundirlos haria que un OCR malo pareciera fraude.
    """


def character_value(character: str) -> int:
    """Valor de un caracter segun ICAO: digitos su valor, A-Z de 10 a 35.

    Curiosidad util para quien venga a tocar esto: el +10 de las letras es
    **invisible** para `check_digit`, porque desplaza la suma en 10 por el
    peso y 70, 30 y 10 son todos cero modulo 10.  Una version con las
    letras valiendo de 0 a 25 calcularia exactamente los mismos digitos de
    control y ningun test sobre MRZ podria distinguirla.

    Se mantiene el valor correcto de todas formas, y con un test propio,
    porque en cuanto alguien use esta funcion para algo que no sea la suma
    modulo 10 el error dejaria de ser invisible.
    """
    if character == "<":
        return 0
    if character.isdigit():
        return int(character)
    if "A" <= character <= "Z":
        return ord(character) - ord("A") + 10
    raise MrzError(f"caracter no valido en una MRZ: {character!r}")


def check_digit(payload: str) -> int:
    """Digito de control de ICAO 9303: pesos 7-3-1 ciclicos, suma modulo 10."""
    total = sum(
        character_value(character) * _WEIGHTS[index % 3]
        for index, character in enumerate(payload)
    )
    return total % 10


@dataclass(frozen=True)
class CheckResult:
    name: str
    printed: str
    computed: int

    @property
    def ok(self) -> bool:
        return self.printed == str(self.computed)


@dataclass(frozen=True)
class MrzData:
    document_type: str
    issuing_country: str
    document_number: str
    optional_data_upper: str
    birth_date_raw: str
    sex: str
    expiry_date_raw: str
    nationality: str
    optional_data_middle: str
    surnames: str
    given_names: str
    checks: tuple[CheckResult, ...]

    @property
    def checks_ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if not check.ok)

    @property
    def full_name(self) -> str:
        return f"{self.given_names} {self.surnames}".strip()

    @property
    def identity_number(self) -> str:
        """El NUIP, que en la cedula colombiana viaja en el campo opcional
        de la segunda linea y se repite impreso en el anverso."""
        return self.optional_data_middle.replace("<", "")

    @property
    def birth_date(self) -> date | None:
        return parse_mrz_date(self.birth_date_raw, prefer_past=True)

    @property
    def expiry_date(self) -> date | None:
        return parse_mrz_date(self.expiry_date_raw, prefer_past=False)


def parse_mrz_date(raw: str, *, prefer_past: bool, today: date | None = None) -> date | None:
    """Convierte un AAMMDD de la MRZ en fecha.

    La MRZ solo trae dos digitos de ano, asi que el siglo hay que
    deducirlo.  No se usa la ventana deslizante generica de ICAO porque
    aqui se conoce el campo: una fecha de nacimiento nunca esta en el
    futuro y una de expiracion casi nunca esta muy en el pasado.  Esa
    diferencia es la que resuelve el caso ambiguo de '36', que es 2036
    como expiracion y 1936 como nacimiento.
    """
    if len(raw) != 6 or not raw.isdigit():
        return None

    today = today or date.today()
    year_short, month, day = int(raw[:2]), int(raw[2:4]), int(raw[4:6])

    for century in (1900, 2000):
        try:
            candidate = date(century + year_short, month, day)
        except ValueError:
            continue
        if prefer_past and candidate <= today:
            return candidate
        if not prefer_past and candidate >= today:
            return candidate

    # Ninguna de las dos encaja con la preferencia: se devuelve la que
    # exista, para que un documento caducado siga siendo legible.
    for century in (2000, 1900):
        try:
            return date(century + year_short, month, day)
        except ValueError:
            continue
    return None


def parse_mrz(lines: list[str] | str) -> MrzData:
    if isinstance(lines, str):
        lines = [line.strip() for line in lines.splitlines() if line.strip()]

    if len(lines) != TD1_LINE_COUNT:
        raise MrzError(
            f"una MRZ TD1 tiene {TD1_LINE_COUNT} lineas, se recibieron {len(lines)}"
        )
    for index, line in enumerate(lines, start=1):
        if len(line) != TD1_LINE_LENGTH:
            raise MrzError(
                f"la linea {index} tiene {len(line)} caracteres y deberia tener "
                f"{TD1_LINE_LENGTH}"
            )
        if not _VALID_CHARACTERS.match(line):
            raise MrzError(f"la linea {index} trae caracteres que no son de MRZ")

    upper, middle, lower = lines

    document_number = upper[5:14]
    document_number_check = upper[14]
    optional_upper = upper[15:30]

    birth_raw = middle[0:6]
    birth_check = middle[6]
    sex = middle[7]
    expiry_raw = middle[8:14]
    expiry_check = middle[14]
    nationality = middle[15:18]
    optional_middle = middle[18:29]
    composite_check = middle[29]

    # El compuesto se calcula sobre los mismos campos que ya llevan digito
    # propio mas los opcionales, de forma que cambiar cualquiera de ellos
    # rompe al menos dos comprobaciones a la vez.
    composite_payload = (
        upper[5:30] + middle[0:7] + middle[8:15] + middle[18:29]
    )

    surnames, _, given = lower.partition("<<")

    return MrzData(
        document_type=upper[0:2].replace("<", ""),
        issuing_country=upper[2:5],
        document_number=document_number.replace("<", ""),
        optional_data_upper=optional_upper.replace("<", ""),
        birth_date_raw=birth_raw,
        sex=sex,
        expiry_date_raw=expiry_raw,
        nationality=nationality,
        optional_data_middle=optional_middle,
        surnames=surnames.replace("<", " ").strip(),
        given_names=given.replace("<", " ").strip(),
        checks=(
            CheckResult("numero_documento", document_number_check,
                        check_digit(document_number)),
            CheckResult("fecha_nacimiento", birth_check, check_digit(birth_raw)),
            CheckResult("fecha_expiracion", expiry_check, check_digit(expiry_raw)),
            CheckResult("compuesto", composite_check, check_digit(composite_payload)),
        ),
    )


def _pad(value: str, length: int) -> str:
    value = value.upper()[:length]
    return value + "<" * (length - len(value))


def build_td1(
    *,
    document_number: str,
    birth_date: date,
    sex: str,
    expiry_date: date,
    identity_number: str,
    surnames: str,
    given_names: str,
    issuing_country: str = "COL",
    nationality: str = "COL",
    document_type: str = "IC",
) -> list[str]:
    """Construye una MRZ TD1 con los digitos de control correctos.

    Vive en el modulo de produccion y no en los tests porque el generador
    de cedulas sinteticas lo necesita: un documento de prueba con la MRZ
    inconsistente seria un caso de fraude sin querer, y entonces el
    conjunto de evaluacion estaria etiquetado mal desde el principio.
    """
    number = _pad(document_number, 9)
    birth_raw = birth_date.strftime("%y%m%d")
    expiry_raw = expiry_date.strftime("%y%m%d")
    optional_middle = _pad(identity_number, 11)

    upper = (
        _pad(document_type, 2)
        + _pad(issuing_country, 3)
        + number
        + str(check_digit(number))
        + "<" * 15
    )
    middle_head = (
        birth_raw
        + str(check_digit(birth_raw))
        + sex.upper()
        + expiry_raw
        + str(check_digit(expiry_raw))
        + _pad(nationality, 3)
        + optional_middle
    )
    composite = check_digit(
        upper[5:30] + middle_head[0:7] + middle_head[8:15] + middle_head[18:29]
    )
    middle = middle_head + str(composite)

    names = f"{surnames.upper().replace(' ', '<')}<<{given_names.upper().replace(' ', '<')}"
    lower = _pad(names, TD1_LINE_LENGTH)

    return [upper, middle, lower]
