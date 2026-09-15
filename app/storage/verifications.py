"""Guardar y recuperar verificaciones.

Una verificacion se escribe entera o no se escribe: la decision, sus
senales y sus fundamentos van en una sola transaccion.  Si se guardaran por
separado, un fallo a mitad dejaria una decision sin las senales que la
sostienen, que es exactamente el registro que no sirve para auditar nada.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.agent.runner import AgentRun
from app.domain.citation_audit import CitationStatus
from app.domain.completeness import adverse_signals
from app.storage.models import (
    verificacion_fundamentos,
    verificacion_senales,
    verificaciones,
)


def hash_imagen(contenido: bytes) -> str:
    """Identifica una imagen sin guardarla; ver la cabecera de models.py."""
    return hashlib.sha256(contenido).hexdigest()


@dataclass(frozen=True)
class VerificacionGuardada:
    id: uuid.UUID
    decision: str
    resultado: str


def guardar(
    engine: sa.Engine,
    run: AgentRun,
    *,
    anverso_sha256: str,
    reverso_sha256: str,
) -> VerificacionGuardada:
    verificacion_id = uuid.uuid4()

    # Que una senal juegue en contra no depende de que el agente llegara a
    # contestar: se mide sobre las senales, no sobre la decision.  Sacarlas
    # del informe de completitud las perdia justo en las verificaciones sin
    # decision, que son las que acaban en manos de un analista humano y en
    # las que saber que hay algo raro es lo unico que hay.
    adversas = set(adverse_signals(run.signals))
    # Lo omitido si necesita una decision: sin fundamentos no hay nada que
    # se haya callado, porque no se dijo nada.
    omitidas = set(run.completeness.omitted) if run.completeness else set()

    filas_senales = [
        {
            "verificacion_id": verificacion_id,
            "signal_id": senal.id,
            "tipo": senal.kind.value,
            # str() y no el valor crudo: la columna guarda lo que vio el
            # agente, que fue texto. Ver la nota de la columna en models.py.
            "valor": None if not senal.available else str(senal.value),
            "disponible": senal.available,
            "motivo_indisponible": senal.unavailable_reason,
            "adversa": senal.id in adversas,
            "omitida": senal.id in omitidas,
        }
        for senal in run.signals
    ]

    filas_fundamentos: list[dict] = []
    if run.decision is not None:
        # Los fundamentos y los resultados de la auditoria van emparejados
        # por posicion porque `audit_citations` recorre los fundamentos en
        # orden y devuelve un resultado por cada uno. Si eso dejara de ser
        # cierto, el registro atribuiria a una cita el veredicto de otra, asi
        # que se comprueba en vez de confiarse.
        resultados = run.audit.results if run.audit is not None else []
        if len(resultados) != len(run.decision.groundings):
            raise ValueError(
                "la auditoria no trae un resultado por fundamento: "
                f"{len(resultados)} frente a {len(run.decision.groundings)}"
            )

        for orden, (fundamento, resultado) in enumerate(
            zip(run.decision.groundings, resultados, strict=True)
        ):
            if resultado.signal_id != fundamento.signal_id:
                raise ValueError(
                    "el resultado de la auditoria no corresponde al fundamento: "
                    f"{resultado.signal_id!r} frente a {fundamento.signal_id!r}"
                )
            filas_fundamentos.append(
                {
                    "verificacion_id": verificacion_id,
                    "orden": orden,
                    "signal_id": fundamento.signal_id,
                    "valor_citado": (
                        None
                        if fundamento.cited_value is None
                        else str(fundamento.cited_value)
                    ),
                    "peso": fundamento.weight.value,
                    "texto": fundamento.text,
                    "estado_auditoria": resultado.status.value,
                }
            )

    cabecera = {
        "id": verificacion_id,
        "modelo": run.model,
        "resultado": run.outcome.value,
        "decision": run.effective_decision.value,
        "resumen": run.decision.summary if run.decision is not None else None,
        "error": run.error,
        "desde_cache": run.from_cache,
        "anverso_sha256": anverso_sha256,
        "reverso_sha256": reverso_sha256,
        "citas_validas": sum(
            1 for fila in filas_fundamentos
            if fila["estado_auditoria"] == CitationStatus.VALID.value
        ),
        "citas_totales": len(filas_fundamentos),
        "explicacion_fiel": run.faithful,
        "explicacion_completa": run.complete,
    }

    with engine.begin() as conexion:
        conexion.execute(sa.insert(verificaciones), cabecera)
        if filas_senales:
            conexion.execute(sa.insert(verificacion_senales), filas_senales)
        if filas_fundamentos:
            conexion.execute(sa.insert(verificacion_fundamentos), filas_fundamentos)

    return VerificacionGuardada(
        id=verificacion_id,
        decision=cabecera["decision"],
        resultado=cabecera["resultado"],
    )


def obtener(engine: sa.Engine, verificacion_id: uuid.UUID) -> dict | None:
    """El registro completo de una verificacion, o None si no existe.

    Devuelve las senales y los fundamentos junto a la cabecera porque una
    decision sin ellos no se puede auditar, y separarlos en tres llamadas
    invitaria a mirar solo la primera.
    """
    with engine.connect() as conexion:
        cabecera = conexion.execute(
            sa.select(verificaciones).where(verificaciones.c.id == verificacion_id)
        ).mappings().first()

        if cabecera is None:
            return None

        senales = conexion.execute(
            sa.select(verificacion_senales)
            .where(verificacion_senales.c.verificacion_id == verificacion_id)
            .order_by(verificacion_senales.c.signal_id)
        ).mappings().all()

        fundamentos = conexion.execute(
            sa.select(verificacion_fundamentos)
            .where(verificacion_fundamentos.c.verificacion_id == verificacion_id)
            .order_by(verificacion_fundamentos.c.orden)
        ).mappings().all()

    return {
        **dict(cabecera),
        "senales": [dict(fila) for fila in senales],
        "fundamentos": [dict(fila) for fila in fundamentos],
    }


def resumen(engine: sa.Engine) -> dict:
    """Las cuentas de todas las verificaciones registradas.

    Existe para responder la pregunta que docs/adr/0004 deja planteada y sin
    responder: **cuantas solicitudes acaban en revision humana, y cuantas de
    esas por un fallo del modelo y no por el documento.**  Un agente que
    incumple el contrato en uno de cada tres casos manda un tercio de las
    solicitudes a un analista, y eso lo descalifica por mucho que acierte en
    el resto.  Sin este recuento ese numero no se ve por ningun lado.

    Se devuelven **recuentos y no porcentajes**.  Sobre cinco verificaciones
    un porcentaje es una cifra con aspecto de medida que no mide nada, y
    quien la lea de reojo la tratara como si midiera.  El denominador va
    delante para que no se pueda leer el numerador sin el.
    """
    with engine.connect() as conexion:
        total = conexion.execute(
            sa.select(sa.func.count()).select_from(verificaciones)
        ).scalar_one()

        por_decision = dict(
            conexion.execute(
                sa.select(verificaciones.c.decision, sa.func.count())
                .group_by(verificaciones.c.decision)
                .order_by(sa.func.count().desc())
            ).all()
        )

        por_resultado = dict(
            conexion.execute(
                sa.select(verificaciones.c.resultado, sa.func.count())
                .group_by(verificaciones.c.resultado)
                .order_by(sa.func.count().desc())
            ).all()
        )

        # Los escalados que NO vienen de un juicio del agente sino de que no
        # llego a haber juicio. Es el numero incomodo del ADR-0004.
        escalados_por_fallo = conexion.execute(
            sa.select(sa.func.count())
            .select_from(verificaciones)
            .where(verificaciones.c.resultado != "decided")
        ).scalar_one()

        # La fidelidad y la completitud solo significan algo sobre las
        # verificaciones en las que el agente llego a explicarse. Meter en el
        # denominador las que nunca tuvieron explicacion mediria la salud del
        # proveedor y lo llamaria calidad del agente.
        con_explicacion = conexion.execute(
            sa.select(sa.func.count())
            .select_from(verificaciones)
            .where(verificaciones.c.resultado == "decided")
        ).scalar_one()

        fieles, completas = conexion.execute(
            sa.select(
                sa.func.count().filter(verificaciones.c.explicacion_fiel),
                sa.func.count().filter(verificaciones.c.explicacion_completa),
            ).where(verificaciones.c.resultado == "decided")
        ).one()

        # Que se calla el agente mas a menudo. Esta es la consulta que
        # justifica haber guardado las omisiones en la fila de cada senal en
        # vez de como una lista de texto en la cabecera.
        mas_calladas = [
            {"signal_id": signal_id, "veces": veces}
            for signal_id, veces in conexion.execute(
                sa.select(
                    verificacion_senales.c.signal_id, sa.func.count().label("veces")
                )
                .where(verificacion_senales.c.omitida.is_(True))
                .group_by(verificacion_senales.c.signal_id)
                .order_by(sa.func.count().desc(), verificacion_senales.c.signal_id)
                .limit(10)
            ).all()
        ]

    return {
        "verificaciones": total,
        "por_decision": por_decision,
        "por_resultado_del_agente": por_resultado,
        "escalados_sin_juicio_del_agente": escalados_por_fallo,
        "con_explicacion": con_explicacion,
        "explicaciones_fieles": fieles,
        "explicaciones_completas": completas,
        "senales_adversas_mas_calladas": mas_calladas,
        "advertencia": (
            "Recuentos, no porcentajes. Fidelidad y completitud se cuentan "
            "solo sobre las verificaciones con explicacion, porque meter en "
            "el denominador las que nunca la tuvieron mediria la salud del "
            "proveedor y lo llamaria calidad del agente."
        ),
    }
