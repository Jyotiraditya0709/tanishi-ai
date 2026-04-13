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

from tanishi.autoresearch.benchmark import BenchmarkResult


# Weights — must sum to 1.0
W_QUALITY = 0.60
W_LATENCY = 0.20
W_RELIABILITY = 0.20

# Latency normalization: anything below FAST_MS scores 1.0, anything above SLOW_MS scores 0.0
FAST_MS = 800.0   # voice-mode-fast threshold
SLOW_MS = 8000.0  # painfully slow threshold


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

    score = (
        W_QUALITY * quality_norm
        + W_LATENCY * latency_norm
        + W_RELIABILITY * reliability_norm
    )
    return score
