"""Casos de decision que necesitan caras reales.

Viven aparte del catalogo de 27 casos y no se mezclan con el, por un motivo
que no es de orden sino de reproducibilidad: las fotos reales **no estan en
el repositorio** y no pueden estarlo.  Si estos casos formaran parte del
catalogo, la suite dejaria de pasar en cualquier maquina que no las tenga y
el conjunto principal dejaria de ser lo que dice ser.

Asi que el catalogo de 27 casos mide **verificacion de documento** sobre
material sintetico y publicable, y esto mide **verificacion de identidad**
sobre material real que no se publica.  Los dos numeros se informan por
separado, con su procedencia, igual que ya se hace con el OCR calibrado
sobre documentos reales.

LO QUE ESTOS CASOS PONEN A PRUEBA DE VERDAD
-------------------------------------------

No es si ArcFace distingue caras: eso ya esta medido y sale en el README.
Es si el agente **sabe usar un numero sin umbral**.

La senal `facial.similarity` llega cruda, y su descripcion le dice al agente
las dos referencias medidas: que sobre 1935 pares de personas distintas
ninguno paso de 0.26, y que el unico par de la misma persona disponible dio
0.79.  Con eso tiene lo necesario para saber que un 0.02 es un desconocido.
Si aun asi aprueba una suplantacion, la decision de no meter el umbral en el
pipeline (ADR-0005) no funciona y hay que revisarla.

POR QUE EL CASO DE SUPLANTACION ES EL QUE IMPORTA
--------------------------------------------------

Es el unico fraude del proyecto que **ninguna otra senal puede ver**.  Un
documento autentico, sin retocar, con sus digitos de control cuadrando y sus
seis cotejos coincidiendo, en manos de otra persona.  La MRZ no lo detecta,
el OCR no lo detecta, la coherencia de fechas no lo detecta.  Solo la cara.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.domain.decision import DecisionKind
from app.evaluation.catalog import person
from app.synthetic.cedula import render_back, render_front

CARAS = Path("data/real/caras")
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

# Cuantos impostores se construyen.  Cada caso cuesta una peticion del cupo
# diario de veinte, y la tanda de calibracion ya se lleva trece: tres
# impostores caben y son suficientes para ver si el agente los rechaza de
# forma consistente o si acerto uno por casualidad.
IMPOSTORES = 3


@dataclass(frozen=True)
class FacialCase:
    id: str
    description: str
    expected_decision: DecisionKind
    reason: str
    front: Image.Image
    back: Image.Image
    selfie: Image.Image
    is_fraud: bool


def _identidades() -> dict[str, list[Path]]:
    if not CARAS.exists():
        return {}
    encontradas: dict[str, list[Path]] = {}
    for carpeta in sorted(
        p for p in CARAS.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        fotos = sorted(
            f for f in carpeta.iterdir() if f.suffix.lower() in EXTENSIONES
        )
        if fotos:
            encontradas[carpeta.name] = fotos
    return encontradas


def build_facial_cases() -> list[FacialCase]:
    """Los casos que el material disponible permite construir.

    Devuelve una lista vacia si no hay fotos, sin quejarse: es el estado
    normal de una maquina recien clonada, y quien ejecute la medicion vera
    el aviso en el script y no una excepcion a mitad de la suite.
    """
    identidades = _identidades()
    if len(identidades) < 2:
        return []

    # La persona con dos fotos es la unica que permite un caso legitimo:
    # una se pinta en el documento y la otra hace de selfie. Si hubiera
    # varias, se tomarian todas.
    con_pareja = [n for n, fotos in identidades.items() if len(fotos) >= 2]
    datos = person()
    casos: list[FacialCase] = []

    for identidad in con_pareja:
        fotos = identidades[identidad]
        casos.append(
            FacialCase(
                id=f"facial-legitimo-{identidad}",
                description="Cedula impecable y la selfie es de su titular.",
                expected_decision=DecisionKind.APPROVE,
                reason=(
                    "El documento cuadra por todos lados y la cara de la "
                    "selfie es la misma que la del retrato. No queda nada "
                    "que objetar, y rechazar o escalar aqui seria perder a "
                    "un cliente legitimo por prudencia mal entendida."
                ),
                front=render_front(datos, portrait=Image.open(fotos[0])),
                back=render_back(datos),
                selfie=Image.open(fotos[1]),
                is_fraud=False,
            )
        )

    # Impostores: el documento de una persona con la selfie de otra.
    #
    # El titular del documento es siempre el mismo -- el que tiene pareja --
    # para que el unico cambio entre el caso legitimo y estos sea la cara de
    # la selfie. Si tambien cambiara el retrato, un fallo podria venir de
    # que ese documento concreto se lee peor.
    if not con_pareja:
        return casos

    titular = con_pareja[0]
    retrato = Image.open(identidades[titular][0])
    otros = [n for n in identidades if n != titular][:IMPOSTORES]

    for otro in otros:
        casos.append(
            FacialCase(
                id=f"facial-suplantacion-{otro}",
                description="Cedula autentica de otra persona.",
                expected_decision=DecisionKind.REJECT,
                reason=(
                    "El documento es autentico y no tiene un solo defecto: "
                    "los digitos de control cuadran, los seis cotejos entre "
                    "anverso y MRZ coinciden y las fechas son coherentes. "
                    "Quien lo presenta no es su titular, y **ninguna otra "
                    "senal del sistema puede verlo**: ni la MRZ, ni el OCR, "
                    "ni la coherencia de fechas. Solo la cara. Es el caso "
                    "que justifica que exista la senal facial."
                ),
                front=render_front(datos, portrait=retrato),
                back=render_back(datos),
                selfie=Image.open(identidades[otro][0]),
                is_fraud=True,
            )
        )

    return casos
