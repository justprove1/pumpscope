"""NarrativeEngine: frontera del LLM, ciclo de vida y sub-scores (SPEC.md 9).

No se llama a ningun modelo. La parte que importa —que una salida invalida se rechaza
entera— se prueba pasando respuestas construidas a mano, que es exactamente lo que llegaria
de un modelo que no obedece.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from mit_data_models.enums import NarrativeState
from mit_narratives import (
    LlmContractError,
    Mention,
    NarrativeSignals,
    classify,
    cross_platform_spread,
    is_creator_only,
    is_exhausted,
    mention_acceleration,
    mention_velocity,
    narrative_score,
    parse_llm_output,
    spam_probability,
    unique_author_growth,
)

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=20)

VALID = {
    "narrative": "Tesla humanoid robotics",
    "state": "ACCELERATING",
    "score": 87,
    "confidence": 0.76,
    "reasons": [
        "Menciones unicas aumentan 320% en 20 minutos",
        "Tres cuentas verificadas publicaron contenido relacionado",
    ],
}


# --- Frontera del LLM ----------------------------------------------------------------------


def test_the_spec_example_validates() -> None:
    """El ejemplo literal de SPEC.md 9 debe pasar."""
    output = parse_llm_output(json.dumps(VALID))
    assert output.narrative == "Tesla humanoid robotics"
    assert output.state == NarrativeState.ACCELERATING
    assert output.score == 87
    assert len(output.reasons) == 2


def test_free_text_is_rejected() -> None:
    """Un modelo que responde en prosa no se interpreta: se descarta."""
    with pytest.raises(LlmContractError, match="no es JSON valido"):
        parse_llm_output("Creo que esta narrativa esta acelerando bastante.")


def test_json_wrapped_in_prose_is_rejected() -> None:
    """Nada de extraer JSON de dentro de un texto.

    Ser tolerante aqui es entrenar al sistema para aceptar lo que sea, y este es el unico
    punto por el que entra un LLM.
    """
    with pytest.raises(LlmContractError):
        parse_llm_output(f"Aqui tienes el analisis:\n{json.dumps(VALID)}\nEspero que ayude.")


def test_an_extra_field_invalidates_the_whole_response() -> None:
    """El caso que justifica `extra=forbid`: un campo que decide dinero.

    No se ignora el campo de mas: se rechaza la respuesta entera. Aceptarla parcialmente
    seria abrir la puerta a que algun dia alguien lea ese campo.
    """
    with pytest.raises(LlmContractError, match="esquema"):
        parse_llm_output({**VALID, "recommended_size_sol": 5.0})


@pytest.mark.parametrize(
    "mutation",
    [
        {"score": 150},
        {"score": -1},
        {"confidence": 1.5},
        {"confidence": -0.2},
        {"state": "MOONING"},
        {"narrative": ""},
        {"reasons": ["r"] * 11},
    ],
    ids=[
        "score_alto",
        "score_negativo",
        "conf_alta",
        "conf_negativa",
        "estado_inventado",
        "narrativa_vacia",
        "demasiadas_razones",
    ],
)
def test_out_of_contract_values_are_rejected(mutation: dict[str, object]) -> None:
    with pytest.raises(LlmContractError):
        parse_llm_output({**VALID, **mutation})


def test_missing_required_fields_are_rejected() -> None:
    incomplete = {k: v for k, v in VALID.items() if k != "state"}
    with pytest.raises(LlmContractError):
        parse_llm_output(incomplete)


def test_a_json_array_is_not_a_valid_response() -> None:
    with pytest.raises(LlmContractError, match="objeto JSON"):
        parse_llm_output(json.dumps([VALID]))


def test_validated_output_is_immutable() -> None:
    """Una vez validada, nadie puede modificarla aguas abajo."""
    output = parse_llm_output(VALID)
    with pytest.raises((TypeError, ValueError)):
        output.score = 99


# --- Ciclo de vida -------------------------------------------------------------------------


def test_spam_is_never_a_narrative() -> None:
    """Aunque tenga volumen y aceleracion enormes."""
    noisy = NarrativeSignals(
        mention_velocity=50.0,
        mention_acceleration=10.0,
        cross_platform_spread=1.0,
        spam_probability=0.9,
    )
    assert classify(noisy) == NarrativeState.NASCENT


def test_saturation_is_detected_before_calling_it_bullish() -> None:
    """Mucho volumen pero sin aceleracion ni autores nuevos: es el techo.

    Etiquetar esto como alcista es justo el error caro.
    """
    topping = NarrativeSignals(
        mention_velocity=20.0, mention_acceleration=0.0, unique_author_growth=0.0
    )
    assert classify(topping) == NarrativeState.SATURATED


def test_viral_requires_both_acceleration_and_spread() -> None:
    """Acelerar en una sola plataforma no es viral: es un grupo."""
    one_platform = NarrativeSignals(
        mention_velocity=10.0,
        mention_acceleration=5.0,
        unique_author_growth=0.5,
        cross_platform_spread=0.25,
    )
    assert classify(one_platform) == NarrativeState.ACCELERATING

    everywhere = NarrativeSignals(
        mention_velocity=10.0,
        mention_acceleration=5.0,
        unique_author_growth=0.5,
        cross_platform_spread=0.9,
    )
    assert classify(everywhere) == NarrativeState.VIRAL


def test_decay_moves_through_decelerating_to_exhausted() -> None:
    decelerating = NarrativeSignals(mention_velocity=4.0, peak_velocity=10.0)
    assert classify(decelerating) == NarrativeState.DECELERATING

    dead = NarrativeSignals(mention_velocity=0.5, peak_velocity=10.0)
    assert classify(dead) == NarrativeState.EXHAUSTED


def test_reviving_requires_a_previous_exhausted_state() -> None:
    signals = NarrativeSignals(
        mention_velocity=8.0,
        mention_acceleration=3.0,
        unique_author_growth=0.4,
        peak_velocity=10.0,
    )
    assert classify(signals, previous=NarrativeState.EXHAUSTED) == NarrativeState.REVIVING
    # Sin pasado agotado, lo mismo es simplemente aceleracion.
    assert classify(signals, previous=None) != NarrativeState.REVIVING


def test_exhausted_states_trigger_the_eligibility_veto() -> None:
    """SPEC.md 12: narrativa agotada es uno de los 17 vetos duros."""
    assert is_exhausted(NarrativeState.EXHAUSTED)
    assert is_exhausted(NarrativeState.SATURATED)
    assert is_exhausted(NarrativeState.DECELERATING)
    assert not is_exhausted(NarrativeState.ACCELERATING)


def test_classification_is_deterministic() -> None:
    signals = NarrativeSignals(mention_velocity=5.0, mention_acceleration=1.0)
    first = classify(signals)
    for _ in range(30):
        assert classify(signals) == first


# --- La regla del creador -------------------------------------------------------------------


def _mention(
    author: str,
    minutes_ago: int,
    *,
    creator: bool = False,
    platform: str = "x",
    followers: int = 500,
    age: int = 400,
    bot: float = 0.0,
) -> Mention:
    return Mention(
        author_id=author,
        posted_at=NOW - timedelta(minutes=minutes_ago),
        platform=platform,
        followers=followers,
        author_age_days=age,
        is_creator_affiliated=creator,
        bot_probability=bot,
    )


def test_a_narrative_is_never_confirmed_by_the_creator_alone() -> None:
    """SPEC.md 9, literal: no se confirma una narrativa solo con posts del creador."""
    only_creator = [_mention(f"creator{i}", i, creator=True) for i in range(30)]
    assert is_creator_only(only_creator)
    assert narrative_score(only_creator, NOW) == 0.0


def test_creator_posts_do_not_inflate_the_score() -> None:
    """Anadir cien posts del creador no debe mover el score."""
    organic = [_mention(f"user{i}", i % 15, platform=["x", "reddit"][i % 2]) for i in range(12)]
    baseline = narrative_score(organic, NOW)
    padded = [*organic, *[_mention("creador", 1, creator=True) for _ in range(100)]]
    assert narrative_score(padded, NOW) == pytest.approx(baseline, abs=1e-9)


def test_three_independent_authors_are_required() -> None:
    two = [_mention("a", 1), _mention("b", 2)]
    assert is_creator_only(two)
    three = [*two, _mention("c", 3)]
    assert not is_creator_only(three)


# --- Sub-scores ------------------------------------------------------------------------------


def test_velocity_counts_only_authentic_mentions() -> None:
    mentions = [
        _mention("real1", 1),
        _mention("real2", 2),
        _mention("bot", 3, bot=0.95),
        _mention("shill", 4, creator=True),
    ]
    assert mention_velocity(mentions, timedelta(minutes=20)) == pytest.approx(2 / 20)


def test_acceleration_compares_consecutive_windows() -> None:
    ramping = [_mention(f"u{i}", i) for i in range(1, 16)]  # todas en los ultimos 20 min
    assert mention_acceleration(ramping, NOW, WINDOW) > 0


def test_unique_author_growth_measures_new_faces() -> None:
    old = [_mention("veterano", 40)]
    new = [_mention(f"nuevo{i}", 5) for i in range(4)]
    growth = unique_author_growth([*old, *new], NOW, WINDOW)
    assert growth == pytest.approx(1.0)


def test_cross_platform_spread_rewards_diversity() -> None:
    single = [_mention(f"u{i}", i, platform="x") for i in range(6)]
    multi = [
        _mention("a", 1, platform="x"),
        _mention("b", 2, platform="reddit"),
        _mention("c", 3, platform="youtube"),
        _mention("d", 4, platform="telegram"),
    ]
    assert cross_platform_spread(single) < cross_platform_spread(multi)
    assert cross_platform_spread(multi) == pytest.approx(1.0)


def test_fresh_accounts_raise_spam_probability() -> None:
    """El ratio de cuentas nuevas separa atencion real de amplificacion pagada."""
    organic = [_mention(f"u{i}", i, age=800) for i in range(10)]
    astroturf = [_mention(f"n{i}", i, age=2) for i in range(10)]
    assert spam_probability(organic) == 0.0
    assert spam_probability(astroturf) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "mentions",
    [
        [],
        [_mention("a", 1)],
        [_mention(f"u{i}", i, platform=["x", "reddit", "youtube"][i % 3]) for i in range(20)],
        [_mention(f"b{i}", i, bot=0.9, age=1) for i in range(20)],
    ],
    ids=["vacio", "una", "organica", "bots"],
)
def test_narrative_score_stays_in_range(mentions: list[Mention]) -> None:
    """Property test: el score SIEMPRE en 0-100."""
    assert 0.0 <= narrative_score(mentions, NOW) <= 100.0


def test_narrative_score_is_deterministic() -> None:
    mentions = [_mention(f"u{i}", i % 18, platform=["x", "reddit"][i % 2]) for i in range(14)]
    first = narrative_score(mentions, NOW)
    for _ in range(25):
        assert narrative_score(mentions, NOW) == first


def test_bot_traffic_scores_below_organic_traffic() -> None:
    organic = [
        _mention(f"u{i}", i % 18, platform=["x", "reddit"][i % 2], age=500) for i in range(14)
    ]
    bots = [_mention(f"b{i}", i % 18, platform="x", age=1, bot=0.9) for i in range(14)]
    assert narrative_score(bots, NOW) < narrative_score(organic, NOW)
