from .moving_average import MovingAverageCrossover
from .rsi_macd import RSIMACDStrategy
from .momentum import MomentumStrategy

__all__ = ["MovingAverageCrossover", "RSIMACDStrategy", "MomentumStrategy"]

STRATEGY_MAP = {
    "ma":       MovingAverageCrossover,
    "rsi_macd": RSIMACDStrategy,
    "momentum": MomentumStrategy,
}
