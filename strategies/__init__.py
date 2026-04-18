from .moving_average import MovingAverageCrossover
from .rsi_macd import RSIMACDStrategy
from .momentum import MomentumStrategy
from .trend_strength import TrendStrengthStrategy

__all__ = [
    "MovingAverageCrossover",
    "RSIMACDStrategy",
    "MomentumStrategy",
    "TrendStrengthStrategy",
]

STRATEGY_MAP = {
    "ma":             MovingAverageCrossover,
    "rsi_macd":       RSIMACDStrategy,
    "momentum":       MomentumStrategy,
    "trend_strength": TrendStrengthStrategy,
}
