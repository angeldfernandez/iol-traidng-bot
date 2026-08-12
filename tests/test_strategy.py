import numpy as np
import pandas as pd

from iol_bot.indicators import rsi, sma
from iol_bot.strategy import Signal, SmaCrossoverRsiStrategy


def _price_df(closes):
    return pd.DataFrame({"cierre": closes})


def test_hold_when_insufficient_data():
    strategy = SmaCrossoverRsiStrategy(fast_window=5, slow_window=10)
    df = _price_df([100.0] * 5)  # menos que slow_window + 1
    result = strategy.evaluate("GGAL", df)
    assert result.signal == Signal.HOLD
    assert "datos insuficientes" in result.motivo


def test_signal_matches_underlying_trend_and_rsi_logic():
    # Recorrido sintético: baja, sube suavemente y luego se acelera -
    # da lugar a cruces y tramos de tendencia sostenida en distintos puntos.
    rng = np.linspace(0, 6 * np.pi, 120)
    closes = pd.Series(100 + 10 * np.sin(rng) + rng, name="cierre")

    strategy = SmaCrossoverRsiStrategy(fast_window=5, slow_window=15, rsi_window=14)

    for cutoff in range(strategy.slow_window + 1, len(closes) + 1):
        sub_closes = closes.iloc[:cutoff].reset_index(drop=True)
        df = _price_df(sub_closes)
        result = strategy.evaluate("GGAL", df)

        fast = sma(sub_closes, strategy.fast_window)
        slow = sma(sub_closes, strategy.slow_window)
        rsi_series = rsi(sub_closes, strategy.rsi_window)

        fast_curr, slow_curr = fast.iloc[-1], slow.iloc[-1]
        rsi_curr = rsi_series.iloc[-1]

        tendencia_alcista = fast_curr > slow_curr
        tendencia_bajista = fast_curr < slow_curr

        if tendencia_alcista and rsi_curr < strategy.rsi_overbought:
            expected = Signal.BUY
        elif tendencia_bajista or rsi_curr >= strategy.rsi_overbought:
            expected = Signal.SELL
        else:
            expected = Signal.HOLD

        assert result.signal == expected, f"cutoff={cutoff}: esperado {expected}, obtuvo {result.signal}"
        assert result.precio == sub_closes.iloc[-1]


def test_buy_fires_while_trend_is_already_established_not_only_at_the_cross():
    # Comportamiento clave del cambio: la tendencia alcista sostenida debe seguir dando BUY varios
    # períodos después del cruce, no solo el día exacto en que la SMA rápida cruzó a la lenta.
    closes = pd.Series([100 + i * 0.5 for i in range(60)], dtype=float)  # suba suave y sostenida
    # rsi_overbought=101 aísla el comportamiento de tendencia bajo prueba: una suba monótona
    # satura el RSI cerca de 100 rápido, y no es eso lo que este test quiere verificar.
    strategy = SmaCrossoverRsiStrategy(fast_window=5, slow_window=15, rsi_window=14, rsi_overbought=101)

    resultado_dia_30 = strategy.evaluate("GGAL", _price_df(closes.iloc[:31]))
    resultado_dia_59 = strategy.evaluate("GGAL", _price_df(closes))

    assert resultado_dia_30.signal == Signal.BUY
    assert resultado_dia_59.signal == Signal.BUY  # muchos períodos después del cruce, sigue en BUY
    assert "tendencia vigente" in resultado_dia_59.motivo


def test_sell_signal_on_overbought_rsi_even_without_crossover():
    # Suba sostenida y pronunciada: RSI llega a sobrecompra manteniendo fast > slow todo el tramo.
    closes = pd.Series([100 + i * 3 for i in range(40)], dtype=float)
    strategy = SmaCrossoverRsiStrategy(fast_window=5, slow_window=15, rsi_window=14, rsi_overbought=70)

    result = strategy.evaluate("GGAL", _price_df(closes))
    assert result.signal == Signal.SELL
    assert "RSI" in result.motivo
