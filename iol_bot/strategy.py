from dataclasses import dataclass
from enum import Enum

from iol_bot.indicators import rsi, sma


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    simbolo: str
    signal: Signal
    precio: float
    motivo: str


class Strategy:
    """Interfaz base: cualquier estrategia recibe un DataFrame de precios y devuelve una TradeSignal."""

    def evaluate(self, simbolo, price_df):
        raise NotImplementedError


class SmaCrossoverRsiStrategy(Strategy):
    """Cruce de medias móviles (rápida/lenta) confirmado con RSI.

    - Señal de compra: la SMA rápida está por encima de la lenta (tendencia alcista vigente, no
      solo en el instante exacto del cruce) y el RSI no está sobrecomprado.
    - Señal de venta: la SMA rápida está por debajo de la lenta, o el RSI está sobrecomprado.

    Nota de diseño: exigir solo el instante del cruce (en vez de la tendencia vigente) es más
    selectivo pero deja pasar la mayoría de las oportunidades — en una prueba real, de 49 símbolos
    con 21 en tendencia alcista, exigir el cruce exacto dio 0 señales BUY. Evaluar la tendencia
    vigente genera señales con más frecuencia a cambio de entrar en tendencias ya en curso (no en
    el arranque exacto); los límites de `risk.py` acotan el tamaño de cada entrada.
    """

    def __init__(self, fast_window=20, slow_window=50, rsi_window=14, rsi_overbought=70, rsi_oversold=30):
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.rsi_window = rsi_window
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def evaluate(self, simbolo, price_df):
        min_rows = self.slow_window + 1
        if len(price_df) < min_rows:
            return TradeSignal(simbolo, Signal.HOLD, precio=None, motivo="datos insuficientes")

        close = price_df["cierre"]
        fast = sma(close, self.fast_window)
        slow = sma(close, self.slow_window)
        rsi_series = rsi(close, self.rsi_window)

        precio_actual = float(close.iloc[-1])
        rsi_actual = float(rsi_series.iloc[-1])

        fast_prev, fast_curr = fast.iloc[-2], fast.iloc[-1]
        slow_prev, slow_curr = slow.iloc[-2], slow.iloc[-1]

        tendencia_alcista = fast_curr > slow_curr
        tendencia_bajista = fast_curr < slow_curr
        cruce_reciente = (fast_prev <= slow_prev) != (fast_curr <= slow_curr)

        if tendencia_alcista and rsi_actual < self.rsi_overbought:
            detalle = "cruce reciente" if cruce_reciente else "tendencia vigente"
            motivo = f"SMA{self.fast_window}>SMA{self.slow_window} ({detalle}), RSI={rsi_actual:.1f}"
            return TradeSignal(simbolo, Signal.BUY, precio_actual, motivo)

        if tendencia_bajista or rsi_actual >= self.rsi_overbought:
            razones = []
            if tendencia_bajista:
                detalle = "cruce reciente" if cruce_reciente else "tendencia vigente"
                razones.append(f"SMA{self.fast_window}<SMA{self.slow_window} ({detalle})")
            if rsi_actual >= self.rsi_overbought:
                razones.append(f"RSI sobrecomprado ({rsi_actual:.1f})")
            motivo = " + ".join(razones) + f", RSI={rsi_actual:.1f}"
            return TradeSignal(simbolo, Signal.SELL, precio_actual, motivo)

        return TradeSignal(simbolo, Signal.HOLD, precio_actual, motivo=f"sin señal, RSI={rsi_actual:.1f}")
