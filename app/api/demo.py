"""Una pantalla que ensena lo que distingue a este sistema: la auditoria.

Un expediente en JSON demuestra que las citas se verifican, pero hay que
leerselo entero para verlo.  La columna de esta pantalla que dice, cita por
cita, si lo que el agente afirmo era cierto, se entiende de un vistazo. Es
lo unico que no tiene cualquier demo de "LLM decide algo".

DOS DECISIONES QUE MANDAN SOBRE TODO LO DEMAS
---------------------------------------------

**No gasta cupo. Nunca.**  Esta pantalla sirve exclusivamente respuestas
que ya estan en cache, y cuando un caso no lo esta se niega y lo dice, en
vez de pedirlo.  El motivo es que una demo se abre delante de alguien y se
pulsa varias veces; con veinte peticiones al dia, una pantalla que decide
de verdad se come la medicion del dia en dos clics.  Aqui el riesgo no es
teorico: ya se perdieron peticiones por lanzar una herramienta creyendo
que solo miraba.

**Lo que se ve son decisiones reales del modelo, no un doble.**  Salen de
cache, pero las produjo `gemini-3.5-flash` ante ese mismo juego de senales.
Habria sido mas comodo enchufar el doble de ensayo -responde siempre, a
cualquier cosa- y habria convertido la pantalla en una mentira bonita: lo
que se enseria serian las reglas fijas de la linea base disfrazadas de
agente.

EL BOTON DE ROMPER EL DOCUMENTO
-------------------------------

Ensena lo que un marcador no puede: que la decision se mueve **porque se
movio una senal**, y que el cambio se puede rastrear hasta el cotejo
concreto que paso a `mismatch`.

No muta la imagen en vivo, que costaria una peticion nueva.  Salta al caso
del catalogo que ya representa ese documento retocado, y lo dice.  El
catalogo existe precisamente para eso: cada pareja es el mismo documento
con y sin el retoque.
"""

from __future__ import annotations

import io
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.api.verifications import _abrir, _leer_acotado

from app.agent.cache import ResponseCache
from app.agent.gemini import GeminiClient
from app.agent.prompt import build_prompt
from app.agent.runner import RunOutcome, run_agent
from app.agent.schema import DECISION_RESPONSE_SCHEMA
from app.config import settings
from app.domain.citation_audit import CitationStatus
from app.evaluation.baseline import Thresholds
from app.evaluation.baseline import decide as decide_baseline
from app.evaluation.catalog import TODAY, load_cases, person
from app.synthetic.cedula import render_back, render_front
from app.signals.pipeline import build_signals

router = APIRouter(tags=["demo"])

PAGINA = Path(__file__).resolve().parent.parent / "web" / "index.html"

# Parejas del catalogo que son el mismo documento con y sin retoque.  Es lo
# que hace honesto el boton: no se inventa una mutacion, se salta al caso
# que ya la representa y que ya se midio.
PAREJAS = {
    "legitimo-torcido": ("ambiguo-apellido-difiere-una-letra", "el apellido"),
    "legitimo-jpeg-moderado": ("fraude-fecha-nacimiento-retocada", "la fecha de nacimiento"),
}
# La vuelta atras, para que el boton funcione en los dos sentidos.
PAREJAS_INVERSAS = {roto: (limpio, que) for limpio, (roto, que) in PAREJAS.items()}


# Cuanto mide el lado largo de la vista previa que se guarda del
# documento subido.  No es la imagen que se midio -esa ya cumplio su
# papel y se descarta-, es solo para que quien mira la pantalla vea el
# documento del que se esta hablando.
#
# 720 px sobre un original de 1012 siguen dejando leer un apellido
# retocado, que es lo unico que hay que poder mirar de cerca, y bajan la
# copia a unas decenas de kilobytes.  A 900 px la copia llegaba a pesar
# mas que el PNG original cuando este venia limpio, que es absurdo para
# algo que se ensena a 148 px de alto.
LADO_VISTA_PREVIA = 720


@dataclass(frozen=True)
class Medicion:
    """Lo que queda de un documento subido: sus senales y como se veia.

    La vista previa no es un capricho estetico. La pantalla existe para
    que un humano juzgue una decision, y juzgarla sin ver el documento es
    justo lo que el agente hace -a el se le mandan solo numeros- pero no
    lo que deberia hacer la persona que revisa.
    """

    senales: object
    vistas: dict[str, bytes]


# Las mediciones recien hechas, a la espera de que alguien decida si vale
# la pena preguntarle al agente. Viven en memoria y se pierden al
# reiniciar, que es justo lo que deben hacer: son de una sesion de demo,
# no un registro. El registro de verdad es `POST /verificaciones`, que
# guarda en Postgres.
#
# El tope existe para que una pantalla abierta toda una tarde no se coma
# la memoria del contenedor. Con las vistas previas dentro importa mas
# que antes, asi que es mas corto.
_MEDICIONES: OrderedDict[str, Medicion] = OrderedDict()
MAX_MEDICIONES = 12


def _png(imagen) -> bytes:
    """Aqui si va PNG y no JPEG.

    Una cedula fabricada para imprimir y luego fotografiar pasa por el OCR
    dos veces: la de la pantalla y la de la camara. Meterle perdidas de
    JPEG antes siquiera de imprimirla seria degradarla gratis justo en los
    bordes de las letras pequenas, que es de donde vive el OCR.
    """
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _vista_previa(imagen) -> bytes:
    copia = imagen.convert("RGB")
    copia.thumbnail((LADO_VISTA_PREVIA, LADO_VISTA_PREVIA))
    buffer = io.BytesIO()
    copia.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _recordar(senales, vistas: dict[str, bytes]) -> str:
    ficha = uuid.uuid4().hex
    _MEDICIONES[ficha] = Medicion(senales=senales, vistas=vistas)
    while len(_MEDICIONES) > MAX_MEDICIONES:
        _MEDICIONES.popitem(last=False)
    return ficha


def get_demo_client() -> GeminiClient:
    """El cliente, como dependencia para que los tests puedan sustituirlo.

    Aqui importa mas que en el otro router: lo que hay que poder probar es
    justamente que esta pantalla NO pide nada, y con el cliente real la
    prueba dependeria de que la cache del disco tuviera o no el caso.
    """
    return GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        cache=ResponseCache(),
    )


def _diagnostico(senales) -> list[dict]:
    """Que le pasa a esta captura, en palabras y con el numero al lado.

    Existe por una prueba real. Se fotografio el documento en la pantalla
    de un movil, el sistema pidio otra foto -que era lo correcto- y para
    saber POR QUE hubo que leerse las veintiocho senales una a una. Delante
    de una camara eso no sirve: quien mira no tiene a nadie que se las lea.

    Los cortes salen de `Thresholds`, los mismos que usa la linea base para
    decidir. Copiarlos aqui habria dejado dos juegos de umbrales
    separandose en silencio, y el dia que alguien moviera uno la pantalla
    seguiria explicando la decision con el otro.
    """
    limites = Thresholds()
    problemas: list[dict] = []

    def valor(clave):
        senal = senales.get(clave)
        return senal.value if senal is not None and senal.available else None

    for clave, cara in (("quality.front_sharpness", "anverso"),
                        ("quality.back_sharpness", "reverso")):
        nitidez = valor(clave)
        if nitidez is not None and nitidez < limites.min_sharpness:
            problemas.append({
                "que": f"La foto del {cara} esta movida o desenfocada.",
                "dato": f"nitidez {nitidez}, hace falta {limites.min_sharpness}",
                "arreglo": "Mas luz y mejor pulso. Una camara de movil enfoca de "
                           "cerca; la webcam de un portatil tiene foco fijo y no.",
            })

    reflejo = valor("quality.front_glare")
    if reflejo is not None and reflejo > limites.max_glare:
        problemas.append({
            "que": "Hay un reflejo fuerte sobre el documento.",
            "dato": f"reflejo {reflejo:.2f}, el limite esta en {limites.max_glare}",
            "arreglo": "Luz indirecta. Fotografiar la pantalla de un movil es el "
                       "peor caso: la pantalla espeja. Mejor en papel mate.",
        })

    ausentes = valor("ocr.fields_missing")
    if ausentes:
        problemas.append({
            "que": f"No se leyeron {ausentes} campos del anverso.",
            "dato": f"{ausentes} ausentes, se admiten {limites.max_missing_fields}",
            "arreglo": "Que el documento llene el encuadre y salga derecho.",
        })

    if valor("mrz.readable") is not True:
        senal = senales.get("mrz.checks_ok")
        motivo = senal.unavailable_reason if senal is not None else None
        problemas.append({
            "que": "No se pudo leer la MRZ del reverso.",
            "dato": motivo or "no se reconocieron las tres lineas",
            "arreglo": "Es lo que mas resolucion pide de todo el documento: son "
                       "caracteres diminutos. Acercarse hasta que ocupe el ancho "
                       "del encuadre.",
        })

    facial = senales.get("facial.similarity")
    if facial is not None and not facial.available:
        problemas.append({
            "que": "No hubo cotejo facial.",
            "dato": facial.unavailable_reason or "no disponible",
            "arreglo": "Una sola cara en la selfie, de frente y con luz.",
        })

    return problemas


def _senal_a_json(senal) -> dict:
    return {
        "id": senal.id,
        "tipo": senal.kind.value,
        "descripcion": senal.description,
        "valor": senal.value,
        "disponible": senal.available,
        "motivo_no_disponible": senal.unavailable_reason,
    }


@router.get("/", include_in_schema=False)
def pagina() -> FileResponse:
    return FileResponse(PAGINA, media_type="text/html")


@router.get(
    "/demo/casos",
    summary="Los casos que la pantalla puede ensenar sin gastar cupo",
)
def listar_casos(modelo: GeminiClient = Depends(get_demo_client)) -> dict:
    """Solo calibracion.

    El reservado se queda fuera aunque tambien tenga casos en cache. No es
    por el cupo -de cache sale gratis- sino porque una pantalla que invita
    a pulsar es la forma mas facil de acabar mirando el reservado "solo
    para ver como va", que es justo lo que la particion existe para
    impedir.
    """
    casos = []
    for caso in sorted(load_cases(), key=lambda c: c.id):
        senales = build_signals(*caso.build(), today=TODAY)
        casos.append(
            {
                "id": caso.id,
                "esperado": caso.expected_decision.value,
                "en_cache": modelo.is_cached(
                    build_prompt(senales), DECISION_RESPONSE_SCHEMA
                ),
                "pareja": PAREJAS.get(caso.id, PAREJAS_INVERSAS.get(caso.id)),
            }
        )
    return {"modelo": settings.gemini_model, "casos": casos}


@router.get(
    "/demo/casos/{caso_id}",
    summary="El expediente completo de un caso, con cada cita contrastada",
)
def ver_caso(
    caso_id: str, modelo: GeminiClient = Depends(get_demo_client)
) -> dict:
    caso = next((c for c in load_cases() if c.id == caso_id), None)
    if caso is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no hay ningun caso {caso_id!r} en calibracion",
        )

    senales = build_signals(*caso.build(), today=TODAY)

    # La puerta. Sin esto, abrir la pantalla en un caso sin cachear
    # lanzaria una peticion de verdad, y una demo se pulsa muchas veces.
    if not modelo.is_cached(build_prompt(senales), DECISION_RESPONSE_SCHEMA):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{caso_id} no esta en cache para {settings.gemini_model}. Esta "
                "pantalla no pide nada al modelo: medir un caso nuevo se hace "
                "con scripts/evaluate_agent.py --gastar, que ensena antes lo "
                "que va a costar."
            ),
        )

    run = run_agent(senales, modelo)

    if run.outcome is not RunOutcome.DECIDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"la respuesta guardada de {caso_id} no produjo decision: {run.error}",
        )

    por_senal = {r.signal_id: r for r in run.audit.results}
    fundamentos = []
    for fundamento in run.decision.groundings:
        resultado = por_senal.get(fundamento.signal_id)
        fundamentos.append(
            {
                "senal": fundamento.signal_id,
                "peso": fundamento.weight.value,
                "texto": fundamento.text,
                "valor_citado": fundamento.cited_value,
                # Lo que hace distinta a esta pantalla: no se muestra lo que
                # el agente dijo, se muestra si era verdad.
                "verificada": resultado is not None and resultado.valid,
                "estado": resultado.status.value if resultado else "sin_contrastar",
                "valor_real": resultado.actual_value if resultado else None,
            }
        )

    # El salto va en los dos sentidos y el boton no puede decir lo mismo:
    # desde el documento intacto se rompe, desde el retocado se repara.
    if caso_id in PAREJAS:
        pareja, sentido = PAREJAS[caso_id], "romper"
    elif caso_id in PAREJAS_INVERSAS:
        pareja, sentido = PAREJAS_INVERSAS[caso_id], "reparar"
    else:
        pareja, sentido = None, None
    # Que habria decidido la linea base de reglas fijas ante estas mismas
    # senales. Es la mitad que faltaba: sin ella la pantalla ensena que el
    # agente acierta, pero no que acierte donde las reglas no llegan, que
    # es la afirmacion que sostiene el proyecto entero. No cuesta nada,
    # son reglas en Python, asi que se calcula siempre.
    base = decide_baseline(senales)

    return {
        "caso": caso_id,
        "modelo": run.model,
        "imagenes": {
            "anverso": f"/demo/casos/{caso_id}/imagen/anverso",
            "reverso": f"/demo/casos/{caso_id}/imagen/reverso",
        },
        "linea_base": {
            "decision": base.decision.value,
            "acierta": base.decision is caso.expected_decision,
            "resumen": base.summary,
        },
        "esperado": caso.expected_decision.value,
        "decision": run.decision.decision.value,
        "acierta": run.decision.decision is caso.expected_decision,
        "resumen": run.decision.summary,
        "fundamentos": fundamentos,
        "auditoria": {
            "citas": len(run.audit.results),
            "validas": sum(1 for r in run.audit.results if r.valid),
            "fiel": run.audit.faithful,
            "completa": run.complete,
        },
        "senales": [_senal_a_json(s) for s in senales],
        "senales_totales": len(senales),
        "pareja": (
            {"caso": pareja[0], "que_cambia": pareja[1], "sentido": sentido}
            if pareja
            else None
        ),
        "estados_posibles": [e.value for e in CitationStatus],
    }


def _expediente(run, senales, *, caso=None) -> dict:
    """La forma que entiende la pantalla, venga de un caso o de una subida."""
    por_senal = {r.signal_id: r for r in run.audit.results}
    fundamentos = []
    for fundamento in run.decision.groundings:
        resultado = por_senal.get(fundamento.signal_id)
        fundamentos.append(
            {
                "senal": fundamento.signal_id,
                "peso": fundamento.weight.value,
                "texto": fundamento.text,
                "valor_citado": fundamento.cited_value,
                "verificada": resultado is not None and resultado.valid,
                "estado": resultado.status.value if resultado else "sin_contrastar",
                "valor_real": resultado.actual_value if resultado else None,
            }
        )
    base = decide_baseline(senales)
    return {
        "modelo": run.model,
        "decision": run.decision.decision.value,
        "resumen": run.decision.summary,
        "fundamentos": fundamentos,
        "linea_base": {
            "decision": base.decision.value,
            "acierta": None if caso is None else base.decision is caso.expected_decision,
            "resumen": base.summary,
        },
        "auditoria": {
            "citas": len(run.audit.results),
            "validas": sum(1 for r in run.audit.results if r.valid),
            "fiel": run.audit.faithful,
            "completa": run.complete,
        },
        "senales": [_senal_a_json(s) for s in senales],
        "senales_totales": len(senales),
    }


@router.get(
    "/demo/casos/{caso_id}/imagen/{cara}",
    summary="El anverso o el reverso de un caso, en PNG",
)
def imagen_del_caso(caso_id: str, cara: str) -> Response:
    """Para poder descargar un documento, retocarlo a mano y volver a subirlo.

    Es lo que convierte la pantalla en una demostracion en vivo en vez de
    una galeria: sin esto hay que traer una cedula de algun sitio, y las
    reales no pueden entrar al repositorio. Con esto se descarga el
    anverso, se le cambia una letra al apellido en cualquier editor y se
    sube: el cotejo pasa a `mismatch` delante de quien mira.
    """
    if cara not in ("anverso", "reverso"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="cara desconocida")
    caso = next((c for c in load_cases() if c.id == caso_id), None)
    if caso is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no hay {caso_id!r}")

    anverso, reverso = caso.build()
    buffer = io.BytesIO()
    (anverso if cara == "anverso" else reverso).save(buffer, format="PNG")
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{caso_id}-{cara}.png"'
        },
    )


@router.post(
    "/demo/medir",
    summary="Mide las senales de un documento subido. No gasta cupo.",
)
async def medir(
    anverso: UploadFile = File(...),
    reverso: UploadFile = File(...),
    selfie: UploadFile | None = File(None),
    modelo: GeminiClient = Depends(get_demo_client),
) -> dict:
    """El paso gratis, y el que de verdad se ensena en vivo.

    Separado de decidir a proposito. Medir las senales es codigo normal
    -OCR, digitos de control, cotejos, nitidez, caras- y no cuesta ni una
    peticion, asi que se puede repetir delante de quien mire tantas veces
    como haga falta. Preguntarle al agente cuesta, y por eso se autoriza
    aparte, viendo antes el cupo que queda.

    Un documento subido se evalua contra la fecha de HOY y no contra la
    del catalogo. Es lo correcto -es lo que hara en produccion- y tiene un
    efecto que conviene decir: casi nunca coincidira con una respuesta ya
    guardada, asi que preguntarle al agente costara una peticion de
    verdad.
    """
    bytes_anverso = await _leer_acotado(anverso, "anverso")
    bytes_reverso = await _leer_acotado(reverso, "reverso")
    imagen_anverso = _abrir(anverso, bytes_anverso, "anverso")
    imagen_reverso = _abrir(reverso, bytes_reverso, "reverso")

    imagen_selfie = None
    if selfie is not None and selfie.filename:
        bytes_selfie = await _leer_acotado(selfie, "selfie")
        if bytes_selfie:
            imagen_selfie = _abrir(selfie, bytes_selfie, "selfie")

    senales = build_signals(imagen_anverso, imagen_reverso, selfie=imagen_selfie)
    base = decide_baseline(senales)
    en_cache = modelo.is_cached(build_prompt(senales), DECISION_RESPONSE_SCHEMA)

    vistas = {
        "anverso": _vista_previa(imagen_anverso),
        "reverso": _vista_previa(imagen_reverso),
    }
    if imagen_selfie is not None:
        vistas["selfie"] = _vista_previa(imagen_selfie)
    ficha = _recordar(senales, vistas)

    return {
        "ficha": ficha,
        "imagenes": {
            cara: f"/demo/mediciones/{ficha}/imagen/{cara}" for cara in vistas
        },
        "senales": [_senal_a_json(s) for s in senales],
        "senales_totales": len(senales),
        "con_selfie": imagen_selfie is not None,
        "linea_base": {"decision": base.decision.value, "resumen": base.summary},
        "diagnostico": _diagnostico(senales),
        # Lo que la pantalla necesita para no mentir sobre el coste.
        "en_cache": en_cache,
        "cupo_restante": modelo.budget.remaining(modelo.model),
    }


@router.post(
    "/demo/decidir",
    summary="Le pregunta al agente por una medicion. GASTA una peticion si no esta en cache.",
)
def decidir(
    ficha: str = Form(...), modelo: GeminiClient = Depends(get_demo_client)
) -> dict:
    """El unico sitio de la pantalla que puede gastar cupo.

    Separarlo no es ceremonia: es la misma regla que la de las
    herramientas de evaluacion, donde ver el plan dejo de lanzar la tanda
    despues de que verlo costara tres peticiones de veinte.
    """
    medicion = _MEDICIONES.get(ficha)
    if medicion is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=(
                "esa medicion ya no esta. Viven en memoria y se pierden al "
                "reiniciar la API o despues de unas cuantas. Vuelve a subir "
                "el documento: medir no cuesta nada."
            ),
        )

    senales = medicion.senales
    run = run_agent(senales, modelo)

    if run.outcome is not RunOutcome.DECIDED:
        # No es un error de la pantalla, es el sistema comportandose como
        # esta disenado: sin decision del agente, la solicitud va a
        # revision humana. Ver docs/adr/0004. Se devuelve 200 por eso
        # mismo: el sistema SI decidio.
        return {
            "decidio_el_agente": False,
            "motivo": run.outcome.value,
            "error": run.error,
            "decision": run.effective_decision.value,
            "cupo_restante": modelo.budget.remaining(modelo.model),
            "senales": [_senal_a_json(s) for s in senales],
            "senales_totales": len(senales),
        }

    expediente = _expediente(run, senales)
    expediente["imagenes"] = {
        cara: f"/demo/mediciones/{ficha}/imagen/{cara}" for cara in medicion.vistas
    }
    expediente["decidio_el_agente"] = True
    expediente["desde_cache"] = run.from_cache
    expediente["cupo_restante"] = modelo.budget.remaining(modelo.model)
    return expediente


@router.get(
    "/demo/mediciones/{ficha}/imagen/{cara}",
    summary="La vista previa del documento que se subio en esa medicion",
)
def imagen_de_la_medicion(ficha: str, cara: str) -> Response:
    """Devuelve la copia reducida, nunca la imagen original.

    La original se usa para medir y se tira: guardarla convertiria una
    pantalla de demostracion en un almacen de documentos de identidad, que
    es exactamente lo que el resto del sistema evita a proposito. `POST
    /verificaciones` tampoco las guarda, solo su SHA-256.
    """
    medicion = _MEDICIONES.get(ficha)
    if medicion is None or cara not in medicion.vistas:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="esa vista ya no esta")
    datos = medicion.vistas[cara]
    # Las cedulas fabricadas se guardan en PNG y las vistas previas de un
    # documento subido en JPEG. Se distingue por la firma del fichero en
    # vez de apuntarlo aparte: el dato ya esta en los propios bytes.
    tipo = "image/png" if datos[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return Response(content=datos, media_type=tipo)


@router.post(
    "/demo/cedula",
    summary="Fabrica una cedula sintetica con el retrato que se le pase",
)
async def fabricar_cedula(
    retrato: UploadFile | None = File(None),
) -> dict:
    """Existe para que nadie tenga que grabar su documento de identidad real.

    Una demostracion en video se graba una vez y se queda en internet para
    siempre. Sacar ahi una cedula autentica es regalar el numero, la fecha
    de nacimiento y la MRZ entera de alguien, y ni este sistema ni ningun
    otro puede deshacer eso despues.

    Con esto se fabrica un documento de identidad **falso y coherente**
    -sus digitos de control cuadran, asi que el sistema lo lee entero- con
    la cara que se quiera encima. Se imprime o se ensena en otra pantalla,
    se graba con la camara, y el cotejo facial contra la selfie funciona de
    verdad porque la cara si es la misma. Lo unico inventado es la
    identidad, que es justo lo que no debe salir en un video.

    Los datos son los mismos de siempre, los del catalogo: LAURA WALTEROS,
    NUIP 1.234.567.890. Nadie los va a confundir con los de una persona.

    Sin retrato, el hueco lleva el marcador gris de siempre y no hay cotejo
    facial posible: el detector no encuentra ninguna cara en el documento y
    `facial.similarity` sale no disponible, diciendo por que.
    """
    imagen = None
    if retrato is not None and retrato.filename:
        contenido = await _leer_acotado(retrato, "retrato")
        if contenido:
            imagen = _abrir(retrato, contenido, "retrato")

    datos = person()
    anverso = render_front(datos, portrait=imagen)
    reverso = render_back(datos)

    vistas = {
        "anverso": _png(anverso),
        "reverso": _png(reverso),
    }
    ficha = _recordar(build_signals(anverso, reverso), vistas)
    return {
        "ficha": ficha,
        "con_retrato": imagen is not None,
        "identidad": f"{datos.given_names} {datos.surnames}, NUIP {datos.nuip}",
        "imagenes": {
            cara: f"/demo/mediciones/{ficha}/imagen/{cara}" for cara in vistas
        },
    }
