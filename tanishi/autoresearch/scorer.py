"""
Tanishi Composite Scorer
========================
Combines benchmark metrics into a single number we can optimize.

The score is constructed so that:
  - Quality dominates (we don't want a fast but dumb assistant)
  - Latency matters (Tanishi should feel responsive)
  - Reliability matters (broken tools = useless features)

Current weights (tune these to bias what Tanishi optimizes for):
  quality:     60%
  latency:     20%
  reliability: 20%
"""

import json
from pathlib import Path

from tanishi.autoresearch.benchmark import BenchmarkResult


# Weights default (used if scoring_config.json missing/invalid)
DEFAULT_W_QUALITY = 0.60
DEFAULT_W_LATENCY = 0.20
DEFAULT_W_RELIABILITY = 0.20

SCORING_CONFIG_PATH = Path(__file__).resolve().parent / "scoring_config.json"

# Latency normalization: anything below FAST_MS scores 1.0, anything above SLOW_MS scores 0.0
FAST_MS = 800.0   # voice-mode-fast threshold
SLOW_MS = 8000.0  # painfully slow threshold


def _load_weights() -> tuple[float, float, float]:
    if not SCORING_CONFIG_PATH.exists():
        return DEFAULT_W_QUALITY, DEFAULT_W_LATENCY, DEFAULT_W_RELIABILITY
    try:
        data = json.loads(SCORING_CONFIG_PATH.read_text(encoding="utf-8"))
        q = float(data.get("quality", DEFAULT_W_QUALITY))
        l = float(data.get("latency", DEFAULT_W_LATENCY))
        r = float(data.get("reliability", DEFAULT_W_RELIABILITY))
        total = q + l + r
        if total <= 0:
            return DEFAULT_W_QUALITY, DEFAULT_W_LATENCY, DEFAULT_W_RELIABILITY
        # Normalize to keep scoring robust to minor config drift.
        return q / total, l / total, r / total
    except Exception:
        return DEFAULT_W_QUALITY, DEFAULT_W_LATENCY, DEFAULT_W_RELIABILITY


def normalize_latency(latency_ms: float) -> float:
    """Map latency to a 0..1 score (higher is better, i.e. faster)."""
    if latency_ms <= FAST_MS:
        return 1.0
    if latency_ms >= SLOW_MS:
        return 0.0
    # Linear interpolation between FAST and SLOW
    return 1.0 - (latency_ms - FAST_MS) / (SLOW_MS - FAST_MS)


def composite_score(bench: BenchmarkResult) -> float:
    """Combine the three metrics into a single 0..1 score."""
    if bench is None:
        return 0.0

    quality_norm = max(0.0, min(1.0, bench.quality))
    latency_norm = normalize_latency(bench.latency_ms)
    reliability_norm = max(0.0, min(1.0, bench.reliability))
    w_quality, w_latency, w_reliability = _load_weights()

    score = (
        w_quality * quality_norm
        + w_latency * latency_norm
        + w_reliability * reliability_norm
    )
    return score
