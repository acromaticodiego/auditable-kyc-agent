import pytest
from pydantic import ValidationError

from app.domain.citation_audit import CitationStatus, audit_citations
from app.domain.decision import AgentDecision, DecisionKind, Grounding, Weight
from app.domain.signals import Signal, SignalKind, SignalSet


def build_signals() -> SignalSet:
    return SignalSet(
        [
            Signal(
                id="facial.similarity",
                kind=SignalKind.SCORE,
                description="Similitud entre la cara del documento y la de la selfie.",
                value=0.61,
            ),
            Signal(
                id="ocr.name_confidence",
                kind=SignalKind.CONFIDENCE,
                description="Confianza del OCR al leer el nombre.",
                value=0.94,
            ),
            Signal(
                id="ocr.full_name",
                kind=SignalKind.TEXT,
                description="Nombre completo leido del documento.",
                value="JUAN DIEGO OSSA",
            ),
            Signal(
                id="document.number_format_valid",
                kind=SignalKind.FLAG,
                description="El numero de cedula cumple el formato esperado.",
                value=True,
            ),
            Signal(
                id="quality.blur_regions",
                kind=SignalKind.COUNT,
                description="Regiones del documento por debajo del umbral de nitidez.",
                value=2,
            ),
            Signal(
                id="ocr.expiry_date",
                kind=SignalKind.TEXT,
                description="Fecha de vencimiento del documento.",
                unavailable_reason="el campo quedo fuera del recorte",
            ),
        ]
    )


def decide(*groundings: Grounding, kind: DecisionKind = DecisionKind.ESCALATE_TO_HUMAN):
    return AgentDecision(
        decision=kind,
        groundings=list(groundings),
        summary="Resumen de prueba suficientemente largo.",
    )


def ground(signal_id: str, cited_value=None, weight: Weight = Weight.AGAINST) -> Grounding:
    return Grounding(
        signal_id=signal_id,
        cited_value=cited_value,
        weight=weight,
        text="Fundamento de prueba con longitud suficiente.",
    )


# --- Citas que deben salir validas -----------------------------------------


def test_citas_correctas_dan_una_decision_fiel():
    report = audit_citations(
        decide(
            ground("facial.similarity", 0.61),
            ground("ocr.full_name", "JUAN DIEGO OSSA"),
            ground("document.number_format_valid", True, Weight.IN_FAVOR),
            ground("quality.blur_regions", 2),
        ),
        build_signals(),
    )

    assert report.faithful
    assert [result.status for result in report.results] == [CitationStatus.VALID] * 4


def test_el_texto_se_compara_sin_acentos_ni_mayusculas():
    """Se audita si el modelo repite el valor, no su ortografia."""
    report = audit_citations(
        decide(ground("ocr.full_name", "Juán  diego ossa")),
        build_signals(),
    )

    assert report.faithful


def test_un_score_truncado_a_dos_decimales_sigue_siendo_valido():
    signals = SignalSet(
        [
            Signal(
                id="facial.similarity",
                kind=SignalKind.SCORE,
                description="Similitud facial.",
                value=0.6178,
            )
        ]
    )

    report = audit_citations(decide(ground("facial.similarity", 0.61)), signals)

    assert report.faithful


# --- Citas que deben salir invalidas ---------------------------------------


def test_citar_una_senal_inexistente_es_invencion():
    report = audit_citations(
        decide(ground("facial.liveness_score", 0.88)),
        build_signals(),
    )

    assert not report.faithful
    assert report.invalid[0].status is CitationStatus.UNKNOWN_SIGNAL
    assert report.invalid[0].actual_value is None


def test_atribuir_a_una_senal_un_valor_que_no_tenia():
    report = audit_citations(
        decide(ground("facial.similarity", 0.85)),
        build_signals(),
    )

    assert not report.faithful
    result = report.invalid[0]
    assert result.status is CitationStatus.VALUE_MISMATCH
    assert (result.cited_value, result.actual_value) == (0.85, 0.61)


def test_la_tolerancia_no_tapa_una_diferencia_de_dos_centesimas():
    """Frontera: 0.011 pasa, 0.012 no.  Si esto se relaja, la metrica deja
    de medir nada."""
    signals = SignalSet(
        [
            Signal(
                id="facial.similarity",
                kind=SignalKind.SCORE,
                description="Similitud facial.",
                value=0.700,
            )
        ]
    )

    assert audit_citations(decide(ground("facial.similarity", 0.689)), signals).faithful
    assert not audit_citations(
        decide(ground("facial.similarity", 0.688)), signals
    ).faithful


def test_citar_una_senal_que_no_se_pudo_calcular():
    """Inventarse una medicion que nunca se hizo es peor que no citarla."""
    report = audit_citations(
        decide(ground("ocr.expiry_date", "2029-04-12")),
        build_signals(),
    )

    assert not report.faithful
    assert report.invalid[0].status is CitationStatus.UNAVAILABLE_SIGNAL


def test_citar_una_senal_sin_decir_cuanto_valia_cuenta_como_invalida():
    """Si esto saliera gratis, no citar valores seria la estrategia segura."""
    report = audit_citations(
        decide(ground("facial.similarity", None)),
        build_signals(),
    )

    assert not report.faithful
    assert report.invalid[0].status is CitationStatus.MISSING_CITED_VALUE


def test_un_contador_se_compara_exacto():
    report = audit_citations(
        decide(ground("quality.blur_regions", 3)),
        build_signals(),
    )

    assert report.invalid[0].status is CitationStatus.VALUE_MISMATCH


def test_una_bandera_invertida_es_invalida():
    report = audit_citations(
        decide(ground("document.number_format_valid", False)),
        build_signals(),
    )

    assert report.invalid[0].status is CitationStatus.VALUE_MISMATCH


# --- El contrato de la decision --------------------------------------------


def test_una_decision_sin_fundamentos_no_es_valida():
    """Sin este minimo, no citar nada daria fidelidad perfecta."""
    with pytest.raises(ValidationError):
        AgentDecision(
            decision=DecisionKind.APPROVE,
            groundings=[],
            summary="Resumen de prueba suficientemente largo.",
        )


def test_aprobar_exige_algun_fundamento_a_favor():
    with pytest.raises(ValidationError, match="a favor"):
        decide(
            ground("facial.similarity", 0.61, Weight.AGAINST),
            kind=DecisionKind.APPROVE,
        )


def test_rechazar_citando_solo_senales_a_favor_es_incoherente():
    with pytest.raises(ValidationError, match="en contra"):
        decide(
            ground("facial.similarity", 0.61, Weight.IN_FAVOR),
            kind=DecisionKind.REJECT,
        )


def test_se_puede_decidir_en_contra_de_una_senal_favorable():
    """Escalar pese a una senal a favor es legitimo y debe poder leerse."""
    decision = decide(
        ground("ocr.name_confidence", 0.94, Weight.IN_FAVOR),
        ground("facial.similarity", 0.61, Weight.AGAINST),
    )

    assert decision.decision is DecisionKind.ESCALATE_TO_HUMAN


def test_una_senal_no_disponible_no_puede_traer_valor():
    with pytest.raises(ValueError, match="no esta disponible"):
        Signal(
            id="ocr.expiry_date",
            kind=SignalKind.TEXT,
            description="Fecha de vencimiento.",
            value="2029-04-12",
            unavailable_reason="campo ilegible",
        )
