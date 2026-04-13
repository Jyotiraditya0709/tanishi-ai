# Tanishi Autoresearch

Self-improvement engine for Tanishi, inspired by Karpathy's autoresearch
(github.com/karpathy/autoresearch).

While you sleep, Tanishi:
1. Picks an aspect of herself to improve (system prompt, routing, memory, tools, voice)
2. Mutates her own config files
3. Runs a benchmark suite to measure the change
4. KEEPS the change if it helps, REVERTS if it doesn't
5. Logs everything to results.tsv
6. Repeats. ~12 experiments per hour.

You wake up to a smarter Tanishi.

## Files

```
tanishi/autoresearch/
  __init__.py
  autoresearch.py     # main loop — run this
  benchmark.py        # the benchmark suite (8 tasks, ~3 min)
  mutator.py          # proposes + applies mutations to config files
  scorer.py           # combines quality/latency/reliability into one number
  setup_configs.py    # one-time: creates the config files mutator can edit
```

## Setup (one time)

1. Drop this folder into `tanishi/autoresearch/` in your project.
2. Create the mutable config files:
   ```
   python -m tanishi.autoresearch.setup_configs
   ```
3. Make sure `tanishi/brain/brain.py` has a `Brain` class with a `respond(prompt, timeout)` method.
   If yours uses a different interface, edit `benchmark.py:run_task` accordingly.
4. Establish a baseline:
   ```
   python -m tanishi.autoresearch.autoresearch --establish-baseline
   ```

## Run

Overnight loop (forever until you Ctrl+C):
```
python -m tanishi.autoresearch.autoresearch
```

Limit to N experiments:
```
python -m tanishi.autoresearch.autoresearch --max-experiments 50
```

Focus on one area:
```
python -m tanishi.autoresearch.autoresearch --area system_prompt
```

## Watch progress

```
tail -f autoresearch_results/results.tsv
```

Or analyze:
```python
import pandas as pd
df = pd.read_csv("autoresearch_results/results.tsv", sep="\t")
print(f"Experiments: {len(df)}, kept: {(df.status=='keep').sum()}")
print(df[df.status=='keep'].sort_values('score', ascending=False).head(10))
```

## Cost

Per experiment: ~$0.10 (8 tasks × Sonnet response + Haiku judging)
12 experiments/hour = ~$1.20/hour
Overnight (8 hours) = ~$10

You can lower this by switching the responder model in routing.py to Haiku.

## Safety

- Every experiment snapshots all mutable files BEFORE running
- If the benchmark crashes, the snapshot is auto-restored
- Only files in MUTABLE_FILES (in autoresearch.py) can be edited
- The composite score has a hard threshold for "keep" — small noise won't cause drift

## Adding new mutations

Edit `mutator.py`. Add a function like:
```python
def mut_my_idea(root: Path):
    f = root / "tanishi/config/something.py"
    text = _read(f)
    if not text:
        return None
    new = text.replace("OLD", "NEW")
    if new == text:
        return None
    return {
        "description": "what this experiment tries",
        "file": str(f),
        "old": text,
        "new": new,
    }
```
Then register it in `MUTATION_LIBRARY` under the right area.
