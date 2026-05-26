"""Agent-based qualitative overlay layer.

Plugs into MultiFactorStrategy as an optional filter: ml_lgb picks top-N
candidates by quant signal → LLM agents review fundamentals + news + sentiment
→ KEEP / DROP decision → final portfolio.

Toggle via strategy yaml `qualitative_overlay.enabled: true`. When the section
is absent or `enabled: false`, strategy behavior is unchanged (zero overhead).
"""

from uni_quant.agents.llm_client import DeepSeekClient, LLMClient
from uni_quant.agents.overlay import (
    OverlayDecision,
    OverlayStats,
    QualitativeOverlay,
)
from uni_quant.agents.toolkit import (
    AkShareToolkit,
    HybridToolkit,
    NewsItem,
    Toolkit,
    TushareToolkit,
)

__all__ = [
    "LLMClient",
    "DeepSeekClient",
    "Toolkit",
    "AkShareToolkit",
    "TushareToolkit",
    "HybridToolkit",
    "NewsItem",
    "QualitativeOverlay",
    "OverlayDecision",
    "OverlayStats",
]
