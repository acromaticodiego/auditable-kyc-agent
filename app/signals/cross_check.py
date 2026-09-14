"""Cotejo entre lo impreso en el anverso y lo que viaja en la MRZ.

Aqui es donde el documento se comprueba contra si mismo. El NUIP, las
fechas y el nombre estan **dos veces** en la cedula, y quien retoca una
foto cambia lo que se ve y se olvida del amasijo de letras del reverso.

Un desacuerdo entre las dos copias no significa fraude por si solo: puede
ser que el OCR leyera mal una de las dos. Por eso cada cotejo viaja con
las dos lecturas y con su procedencia, y la interpretacion se deja para el
agente. Confundir "el documento se contradice" con "el OCR se equivoco"
es exactamente el error que haria rechazar a alguien por una foto mala.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import Enum

from app.signals.mrz import MrzData
from app.signals.ocr_front import (
    FrontFields,
    parse_nuip,
    parse_place_and_date,
    parse_spanish_date,
)


class CrossStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    FRONT_MISSING = "front_missing"
    MRZ_MISSING = "mrz_missing"
    BOTH_MISSING = "both_missing"


@dataclass(frozen=True)
class CrossCheck:
    field: str
    status: CrossStatus
    front_value: str | None
    mrz_value: str | None

    @property
    def comparable(self) -> bool:
        return self.status in (CrossStatus.MATCH, CrossStatus.MISMATCH)


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Za-z ]", " ", text).upper().split())


def _compare_names(front: str, mrz: str) -> bool:
    """Compara nombres teniendo en cuenta que la MRZ se queda corta.

    La tercera linea del TD1 tiene 30 caracteres para apellidos y nombres
    juntos, asi que un nombre largo llega truncado. Exigir igualdad exacta
    marcaria como contradiccion a todas las personas con nombre largo, que
    no es una contradiccion del documento sino un limite del formato.
    """
    front_norm, mrz_norm = _normalize_name(front), _normalize_name(mrz)
    if not front_norm or not mrz_norm:
        return False
    return front_norm.startswith(mrz_norm) or mrz_norm.startswith(front_norm)


def _check(
    field: str,
    front_raw: str | None,
    mrz_raw: str | None,
    equal,
) -> CrossCheck:
    if front_raw is None and mrz_raw is None:
        status = CrossStatus.BOTH_MISSING
    elif front_raw is None:
        status = CrossStatus.FRONT_MISSING
    elif mrz_raw is None:
        status = CrossStatus.MRZ_MISSING
    else:
        status = CrossStatus.MATCH if equal() else CrossStatus.MISMATCH
    return CrossCheck(field, status, front_raw, mrz_raw)


def cross_check(front: FrontFields, mrz: MrzData | None) -> list[CrossCheck]:
    """Enfrenta cada dato duplicado. Devuelve un resultado por campo."""
    front_nuip = parse_nuip(front.value("nuip") or "")
    front_birth = parse_spanish_date(front.value("birth_date") or "")
    front_expiry = parse_spanish_date(front.value("expiry_date") or "")
    front_surnames = front.value("surnames")
    front_given = front.value("given_names")
    front_sex = (front.value("sex") or "").strip().upper()[:1] or None

    mrz_nuip = mrz.identity_number if mrz else None
    mrz_birth = mrz.birth_date if mrz else None
    mrz_expiry = mrz.expiry_date if mrz else None
    mrz_surnames = mrz.surnames if mrz else None
    mrz_given = mrz.given_names if mrz else None
    mrz_sex = (mrz.sex if mrz else None) or None

    return [
        _check("nuip", front_nuip, mrz_nuip, lambda: front_nuip == mrz_nuip),
        _check(
            "birth_date",
            front_birth.isoformat() if front_birth else None,
            mrz_birth.isoformat() if mrz_birth else None,
            lambda: front_birth == mrz_birth,
        ),
        _check(
            "expiry_date",
            front_expiry.isoformat() if front_expiry else None,
            mrz_expiry.isoformat() if mrz_expiry else None,
            lambda: front_expiry == mrz_expiry,
        ),
        _check(
            "surnames",
            front_surnames,
            mrz_surnames,
            lambda: _compare_names(front_surnames, mrz_surnames),
        ),
        _check(
            "given_names",
            front_given,
            mrz_given,
            lambda: _compare_names(front_given, mrz_given),
        ),
        _check("sex", front_sex, mrz_sex, lambda: front_sex == mrz_sex),
    ]


def is_expired(front: FrontFields, mrz: MrzData | None, today: date) -> bool | None:
    """Si el documento esta vencido, mirando primero la MRZ.

    Se prefiere la fecha de la MRZ porque esa si tiene un digito de control
    detras: si cuadra, la lectura es casi con certeza correcta. La del
    anverso es el respaldo cuando no hay MRZ legible.
    """
    if mrz and mrz.expiry_date:
        return mrz.expiry_date < today

    front_expiry = parse_spanish_date(front.value("expiry_date") or "")
    if front_expiry:
        return front_expiry < today

    issue_date, _ = parse_place_and_date(front.value("issue") or "")
    if issue_date is None:
        return None
    return None
