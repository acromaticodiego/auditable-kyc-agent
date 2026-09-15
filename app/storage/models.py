"""Las tablas donde queda registrada cada verificacion.

Lo que se guarda aqui responde a una pregunta concreta: **¿se puede
reconstruir y discutir esta decision dentro de seis meses, cuando ya nadie
tenga las fotos?**  Si la respuesta es no, el registro no sirve para
auditar, solo para contar cuantas verificaciones hubo.

Por eso se guardan tres cosas y no una:

- la decision y su resumen, que es lo minimo;
- **las senales tal como se midieron**, incluidas las que no se pudieron
  medir y por que.  Sin ellas, la explicacion del agente no se puede
  contrastar mas tarde: quedaria su palabra de que la nitidez era 1505;
- **cada fundamento con el resultado de su auditoria ya calculado**.  Se
  guarda el veredicto y no solo la cita porque el auditor puede cambiar, y
  lo que hay que poder defender es lo que el sistema concluyo *entonces*.

Las imagenes NO se guardan, solo su hash.  Un registro de auditoria que
ademas acumula cedulas y selfies se convierte en el activo mas apetecible
del sistema, y la funcion de auditar no lo necesita: el hash basta para
demostrar que este registro corresponde a esa foto, siempre que alguien
conserve la foto.  Quien necesite las imagenes de verdad (una investigacion
de fraude) las tendra en el sistema que las recibio, con su propia
retencion y su propio control de acceso.

No hay Alembic.  Es un monolito de un solo desarrollador y las tablas se
crean con `ensure_schema()`; cuando el esquema cambie habra que recrearlas
a mano.  Se anota porque es una limitacion real y no una que se vaya a
descubrir sola: en cuanto haya datos que no se puedan perder, esto necesita
migraciones de verdad.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

verificaciones = sa.Table(
    "verificaciones",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    # Que modelo decidio.  Va en cada fila y no en una constante del codigo
    # porque el cupo diario obliga a cambiar de modelo a mitad de una tanda,
    # y una decision sin saber quien la tomo no se puede comparar con otra.
    sa.Column("modelo", sa.Text, nullable=False),
    # Como acabo la vuelta del agente: decided, contract_violation,
    # malformed, unavailable, out_of_quota.  Se guarda aunque no haya
    # decision; si solo se registraran los finales felices, la tasa de
    # escalados por fallo del modelo seria invisible justo en el informe
    # que tendria que enseñarla.
    sa.Column("resultado", sa.Text, nullable=False),
    # La decision que el sistema aplica de verdad, que ante un fallo del
    # agente es escalate_to_human; ver docs/adr/0004.
    sa.Column("decision", sa.Text, nullable=False),
    sa.Column("resumen", sa.Text, nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("desde_cache", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("anverso_sha256", sa.Text, nullable=False),
    sa.Column("reverso_sha256", sa.Text, nullable=False),
    # El resultado de la auditoria, resumido, para no tener que recorrer los
    # fundamentos cada vez que alguien pregunta cuantas decisiones tenian
    # todas sus citas correctas.
    sa.Column("citas_validas", sa.Integer, nullable=False, server_default="0"),
    sa.Column("citas_totales", sa.Integer, nullable=False, server_default="0"),
    sa.Column("explicacion_fiel", sa.Boolean, nullable=False, server_default=sa.false()),
    # Fiel y completa son dos cosas distintas y las dos hacen falta: la
    # primera dice que nada de lo que el agente afirmo es falso, la segunda
    # que no se callo ninguna senal que jugara en contra.  Una explicacion
    # puede ser verdadera entera y esconder lo unico que importaba; ver
    # app/domain/completeness.py.
    sa.Column(
        "explicacion_completa", sa.Boolean, nullable=False, server_default=sa.false()
    ),
)

verificacion_senales = sa.Table(
    "verificacion_senales",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "verificacion_id",
        sa.Uuid,
        sa.ForeignKey("verificaciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("signal_id", sa.Text, nullable=False),
    sa.Column("tipo", sa.Text, nullable=False),
    # El valor va como texto aunque la senal sea numerica o booleana.
    #
    # Es a proposito: lo que se audita es si el agente repitio el valor que
    # se le enseno, y el agente lo recibio renderizado como texto. Guardarlo
    # convertido a float aqui introduciria una diferencia entre lo que vio
    # el modelo y lo que consta en el registro, que es justo la grieta por
    # donde se cuela una auditoria que no prueba nada.
    sa.Column("valor", sa.Text, nullable=True),
    sa.Column("disponible", sa.Boolean, nullable=False),
    sa.Column("motivo_indisponible", sa.Text, nullable=True),
    # Si esta senal, con este valor, jugaba en contra de la solicitud.
    sa.Column("adversa", sa.Boolean, nullable=False, server_default=sa.false()),
    # Si jugaba en contra y la explicacion no la menciono.
    #
    # Va aqui, en la fila de cada senal, y no como una lista en la cabecera,
    # porque asi se puede preguntar lo que de verdad quiere saber un
    # auditor: que se calla el agente mas a menudo. Con una lista de texto
    # esa consulta seria un LIKE sobre una cadena.
    sa.Column("omitida", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.UniqueConstraint("verificacion_id", "signal_id", name="uq_senal_por_verificacion"),
)

verificacion_fundamentos = sa.Table(
    "verificacion_fundamentos",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "verificacion_id",
        sa.Uuid,
        sa.ForeignKey("verificaciones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    # El orden en que el agente los dio.  Importa: el primero suele ser el
    # que de verdad sostiene la decision, y perder el orden convierte la
    # explicacion en un saco de razones sueltas.
    sa.Column("orden", sa.Integer, nullable=False),
    sa.Column("signal_id", sa.Text, nullable=False),
    sa.Column("valor_citado", sa.Text, nullable=True),
    sa.Column("peso", sa.Text, nullable=False),
    sa.Column("texto", sa.Text, nullable=False),
    # El veredicto del auditor sobre ESTA cita: valid, unknown_signal,
    # unavailable_signal, value_mismatch, missing_cited_value.
    sa.Column("estado_auditoria", sa.Text, nullable=False),
    sa.UniqueConstraint(
        "verificacion_id", "orden", name="uq_fundamento_por_verificacion"
    ),
)


def ensure_schema(engine: sa.Engine) -> None:
    """Crea las tablas si no existen.

    No es una migracion y no pretende serlo: no altera tablas que ya
    existan con otra forma. Ver la cabecera del modulo.
    """
    metadata.create_all(engine)
