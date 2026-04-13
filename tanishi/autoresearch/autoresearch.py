"""
Tanishi Autoresearch — Self-Improvement Engine
================================================
Inspired by Karpathy's autoresearch (github.com/karpathy/autoresearch),
adapted for autonomous LLM-agent self-improvement.

While you sleep, Tanishi:
  1. Picks an aspect of herself to improve (prompt, tool config, routing logic)
  2. Mutates her own config files
  3. Runs the benchmark suite to measure the change
  4. KEEPS the change if metrics improved, REVERTS if they didn't
  5. Logs everything to results.tsv
  6. Repeats. Forever. Until you stop her.

Usage:
    python -m tanishi.autoresearch.autoresearch                    # run forever
    python -m tanishi.autoresearch.autoresearch --max-experiments 50
    python -m tanishi.autoresearch.autoresearch --area prompts     # focus on one area

The metric is composite: quality (LLM-judged) + speed (latency) + reliability (tool success rate),
weighted so improvements are meaningful and measurable in ~3 minutes per experiment.
"""

import os
import sys
import json
import time
import shutil
import argparse
import traceback
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict

# Tanishi internals — adjust imports if your project layout differs
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tanishi.autoresearch.benchmark import run_benchmark_suite, BenchmarkResult
from tanishi.autoresearch.mutator import propose_mutation, apply_mutation, revert_mutation
from tanishi.autoresearch.scorer import composite_score

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "autoresearch_results"
RESULTS_TSV = RESULTS_DIR / "results.tsv"
SNAPSHOTS_DIR = RESULTS_DIR / "snapshots"
EXPERIMENTS_LOG = RESULTS_DIR / "experiments.jsonl"

# Experiment time budget — keep short so we get many iterations overnight
TIME_BUDGET_SECONDS = 180   # 3 minutes per experiment
HARD_TIMEOUT_SECONDS = 360  # kill if it exceeds 6 min

# Score improvement threshold to "keep" an experiment
KEEP_THRESHOLD = 0.001  # composite score must improve by at least this much

# Areas Tanishi can experiment with
EXPERIMENT_AREAS = [
    "system_prompt",     # Tanishi's personality/behavior prompt
    "tool_descriptions", # how tools are described to the model
    "routing_logic",     # when to use Claude vs Ollama vs GPT
    "memory_retrieval",  # similarity thresholds, top-k for memory recall
    "voice_params",      # filler timing, chunk sizes, TTS settings
    "tool_params",       # timeouts, retries, max tokens
]

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_workspace():
    """Create directories and initialize results.tsv if missing."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    if not RESULTS_TSV.exists():
        with open(RESULTS_TSV, "w", encoding="utf-8") as f:
            f.write("experiment_id\ttimestamp\tarea\tscore\tquality\tlatency_ms\treliability\tstatus\tdescription\n")
        print(f"[setup] initialized {RESULTS_TSV}")
    else:
        print(f"[setup] using existing {RESULTS_TSV}")

# ---------------------------------------------------------------------------
# Snapshotting (so we can revert any experiment)
# ---------------------------------------------------------------------------

# Files that experiments are allowed to mutate. Anything else is off-limits.
MUTABLE_FILES = [
    "tanishi/config/prompts.py",
    "tanishi/config/personality.py",
    "tanishi/config/routing.py",
    "tanishi/config/tool_params.py",
    "tanishi/config/memory_params.py",
    "tanishi/voice/voice_config.py",
]

def snapshot_state(experiment_id: str) -> Path:
    """Save a copy of all mutable files before an experiment."""
    snap_dir = SNAPSHOTS_DIR / experiment_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    for rel in MUTABLE_FILES:
        src = PROJECT_ROOT / rel
        if src.exists():
            dst = snap_dir / rel.replace("/", "__")
            shutil.copy2(src, dst)
    return snap_dir

def restore_snapshot(snap_dir: Path):
    """Restore all mutable files from a snapshot directory."""
    for snap_file in snap_dir.iterdir():
        rel = snap_file.name.replace("__", "/")
        dst = PROJECT_ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap_file, dst)

# ---------------------------------------------------------------------------
# Results logging
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    experiment_id: str
    timestamp: str
    area: str
    score: float
    quality: float
    latency_ms: float
    reliability: float
    status: str  # "keep" | "discard" | "crash"
    description: str

    def to_tsv_row(self) -> str:
        # Sanitize description (no tabs/newlines)
        desc = self.description.replace("\t", " ").replace("\n", " ")[:200]
        return (
            f"{self.experiment_id}\t{self.timestamp}\t{self.area}\t"
            f"{self.score:.6f}\t{self.quality:.4f}\t{self.latency_ms:.0f}\t"
            f"{self.reliability:.4f}\t{self.status}\t{desc}\n"
        )

def log_result(result: ExperimentResult, mutation_detail: dict):
    """Append to results.tsv and experiments.jsonl"""
    with open(RESULTS_TSV, "a", encoding="utf-8") as f:
        f.write(result.to_tsv_row())
    with open(EXPERIMENTS_LOG, "a", encoding="utf-8") as f:
        record = asdict(result)
        record["mutation"] = mutation_detail
        f.write(json.dumps(record) + "\n")

def load_baseline() -> float | None:
    """Read the most recent KEPT score from results.tsv as the baseline to beat."""
    if not RESULTS_TSV.exists():
        return None
    best = None
    with open(RESULTS_TSV, "r", encoding="utf-8") as f:
        next(f, None)  # header
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 8 and parts[7] == "keep":
                try:
                    score = float(parts[3])
                    if best is None or score > best:
                        best = score
                except ValueError:
                    pass
    return best

# ---------------------------------------------------------------------------
# Single experiment
# ---------------------------------------------------------------------------

def run_one_experiment(experiment_num: int, area: str | None, baseline: float) -> ExperimentResult:
    """Mutate, benchmark, decide keep/discard. Always returns a result (never raises)."""
    experiment_id = f"exp_{int(time.time())}_{experiment_num:04d}"
    timestamp = datetime.utcnow().isoformat()

    print(f"\n{'='*72}")
    print(f"[experiment {experiment_num}] id={experiment_id}")
    print(f"[experiment {experiment_num}] baseline_score={baseline:.6f}")

    # 1. Snapshot current state so we can revert
    snap = snapshot_state(experiment_id)

    # 2. Pick an area and propose a mutation
    chosen_area = area or pick_area(experiment_num)
    print(f"[experiment {experiment_num}] area={chosen_area}")
    try:
        mutation = propose_mutation(chosen_area, history_path=EXPERIMENTS_LOG)
        print(f"[experiment {experiment_num}] proposed: {mutation['description']}")
    except Exception as e:
        print(f"[experiment {experiment_num}] mutation proposal failed: {e}")
        return ExperimentResult(
            experiment_id, timestamp, chosen_area,
            score=0.0, quality=0.0, latency_ms=0.0, reliability=0.0,
            status="crash", description=f"mutation_proposal_failed: {e}",
        )

    # 3. Apply the mutation to actual Tanishi files
    try:
        apply_mutation(mutation, project_root=PROJECT_ROOT)
    except Exception as e:
        print(f"[experiment {experiment_num}] apply failed: {e}")
        restore_snapshot(snap)
        return ExperimentResult(
            experiment_id, timestamp, chosen_area,
            score=0.0, quality=0.0, latency_ms=0.0, reliability=0.0,
            status="crash", description=f"apply_failed: {e}",
        )

    # 4. Run the benchmark suite (this is where we actually measure Tanishi)
    t0 = time.time()
    try:
        bench: BenchmarkResult = run_benchmark_suite(
            time_budget_s=TIME_BUDGET_SECONDS,
            hard_timeout_s=HARD_TIMEOUT_SECONDS,
        )
        elapsed = time.time() - t0
        print(f"[experiment {experiment_num}] benchmark done in {elapsed:.1f}s")
    except Exception as e:
        traceback.print_exc()
        print(f"[experiment {experiment_num}] benchmark crashed: {e}")
        restore_snapshot(snap)
        return ExperimentResult(
            experiment_id, timestamp, chosen_area,
            score=0.0, quality=0.0, latency_ms=0.0, reliability=0.0,
            status="crash", description=f"benchmark_crashed: {e}",
        )

    # 5. Compute composite score and decide
    score = composite_score(bench)
    delta = score - baseline
    keep = delta > KEEP_THRESHOLD

    print(f"[experiment {experiment_num}] quality={bench.quality:.4f}  "
          f"latency={bench.latency_ms:.0f}ms  reliability={bench.reliability:.4f}")
    print(f"[experiment {experiment_num}] score={score:.6f}  delta={delta:+.6f}  "
          f"-> {'KEEP' if keep else 'DISCARD'}")

    if not keep:
        restore_snapshot(snap)
        print(f"[experiment {experiment_num}] reverted to snapshot")

    return ExperimentResult(
        experiment_id=experiment_id,
        timestamp=timestamp,
        area=chosen_area,
        score=score,
        quality=bench.quality,
        latency_ms=bench.latency_ms,
        reliability=bench.reliability,
        status="keep" if keep else "discard",
        description=mutation["description"],
    )

def pick_area(experiment_num: int) -> str:
    """Round-robin through areas, with slight bias toward unexplored ones."""
    return EXPERIMENT_AREAS[experiment_num % len(EXPERIMENT_AREAS)]

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tanishi Autoresearch - self-improvement engine")
    parser.add_argument("--max-experiments", type=int, default=-1,
                        help="Maximum experiments to run (-1 = forever)")
    parser.add_argument("--area", type=str, default=None, choices=EXPERIMENT_AREAS,
                        help="Restrict experiments to one area")
    parser.add_argument("--establish-baseline", action="store_true",
                        help="Run benchmark once on current state and record as baseline")
    args = parser.parse_args()

    setup_workspace()

    print("=" * 72)
    print("  TANISHI AUTORESEARCH — self-improvement loop starting")
    print("=" * 72)
    print(f"  Project root:    {PROJECT_ROOT}")
    print(f"  Results:         {RESULTS_TSV}")
    print(f"  Time budget:     {TIME_BUDGET_SECONDS}s per experiment")
    print(f"  Areas:           {args.area or 'all'}")
    print(f"  Max experiments: {'forever' if args.max_experiments == -1 else args.max_experiments}")
    print("=" * 72)

    # Establish baseline if needed
    baseline = load_baseline()
    if baseline is None or args.establish_baseline:
        print("\n[baseline] no previous baseline, running benchmark on current state...")
        try:
            bench = run_benchmark_suite(
                time_budget_s=TIME_BUDGET_SECONDS,
                hard_timeout_s=HARD_TIMEOUT_SECONDS,
            )
            baseline = composite_score(bench)
            baseline_result = ExperimentResult(
                experiment_id=f"baseline_{int(time.time())}",
                timestamp=datetime.utcnow().isoformat(),
                area="baseline",
                score=baseline,
                quality=bench.quality,
                latency_ms=bench.latency_ms,
                reliability=bench.reliability,
                status="keep",
                description="initial baseline",
            )
            log_result(baseline_result, {"description": "baseline", "diff": None})
            print(f"[baseline] established: {baseline:.6f}")
        except Exception as e:
            print(f"[baseline] FAILED to establish baseline: {e}")
            traceback.print_exc()
            sys.exit(1)

    # Main experiment loop
    experiment_num = 0
    kept_count = 0
    try:
        while args.max_experiments == -1 or experiment_num < args.max_experiments:
            experiment_num += 1
            try:
                result = run_one_experiment(experiment_num, args.area, baseline)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[experiment {experiment_num}] OUTER crash: {e}")
                traceback.print_exc()
                continue

            log_result(result, {"description": result.description})

            if result.status == "keep":
                baseline = result.score
                kept_count += 1
                print(f"[loop] new baseline: {baseline:.6f}  (kept {kept_count}/{experiment_num})")

    except KeyboardInterrupt:
        print(f"\n\n[loop] interrupted by user after {experiment_num} experiments")
        print(f"[loop] kept {kept_count} improvements, final score: {baseline:.6f}")

    print("\n" + "=" * 72)
    print(f"  AUTORESEARCH COMPLETE")
    print(f"  Total experiments: {experiment_num}")
    print(f"  Improvements kept: {kept_count}")
    print(f"  Final score:       {baseline:.6f}")
    print(f"  Results:           {RESULTS_TSV}")
    print("=" * 72)


if __name__ == "__main__":
    main()
