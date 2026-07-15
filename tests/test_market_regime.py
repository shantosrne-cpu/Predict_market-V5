import pytest

from Nertzh import NertzMetalEngine


def test_market_regime_classifies_calm_market() -> None:
    regime = NertzMetalEngine._classify_market_regime(
        {"combined": 1.2, "volatility": 0.0012, "pio": 0.2, "egm": 0.1},
        [],
    )
    assert regime == "calm"


def test_market_regime_classifies_normal_market() -> None:
    regime = NertzMetalEngine._classify_market_regime(
        {"combined": 4.5, "volatility": 0.0055, "pio": 0.7, "egm": 0.6},
        [],
    )
    assert regime == "normal"


def test_market_regime_classifies_volatile_market() -> None:
    regime = NertzMetalEngine._classify_market_regime(
        {"combined": 14.0, "volatility": 0.014, "pio": 2.2, "egm": 1.8},
        [],
    )
    assert regime == "volatile"
