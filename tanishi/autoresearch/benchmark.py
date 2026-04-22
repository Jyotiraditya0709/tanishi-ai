import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

"""
Tanishi Benchmark Suite (PATCHED v2)
"""
import time
import asyncio
import traceback
from dataclasses import dataclass, field

try:
    from tanishi.core.brain import TanishiBrain
    BRAIN_IMPORT_ERROR = None
except ImportError as e:
    TanishiBrain = None
    BRAIN_IMPORT_ERROR = str(e)


class CrashStorm(Exception):
    pass


@dataclass
class BenchmarkTask:
    name: str
    category: str
    prompt: str
    success_criteria: list
    expected_tool: str = None
    timeout_s: float = 30.0


BENCHMARK_TASKS = [
    BenchmarkTask("greeting", "conversation", "hey tanishi how's it going",
                  ["natural greeting", "in character", "concise"]),
    BenchmarkTask("explain_concept", "conversation", "explain RAG in two sentences",
                  ["mentions retrieval", "mentions generation", "under 60 words"]),
    BenchmarkTask("get_time", "tool_use", "what time is it right now?",
                  ["returns current time"], expected_tool="get_datetime"),
    BenchmarkTask("system_check", "tool_use", "how much RAM do I have?",
                  ["returns RAM amount"], expected_tool="get_system_info"),
    BenchmarkTask("memory_recall", "memory",
                  "remember my favorite color is blue. what is my favorite color?",
                  ["recalls blue"]),
    BenchmarkTask("math", "reasoning", "if I save $50/week, how much in 6 months?",
                  ["correct math around 1300"]),
    BenchmarkTask("personality", "personality",
                  "I just finished a really long debugging session, exhausted",
                  ["empathetic", "in character"]),
    BenchmarkTask("poem", "conversation", "write me a quick poem about coffee",
                  ["actually writes a poem"]),
]


@dataclass
class TaskResult:
    name: str
    category: str
    success: bool
    quality_score: float
    latency_ms: float
    error: str = None
    response_text: str = ""


@dataclass
class BenchmarkResult:
    quality: float
    latency_ms: float
    reliability: float
    task_results: list = field(default_factory=list)
    total_time_s: float = 0.0


def judge_response(task, response):
    """Score an AI response 0.0-1.0 using Gemma (local) with Claude fallback."""
    if not response or not response.strip():
        return 0.0

    criteria = "\n".join(f"  - {c}" for c in task.success_criteria)
    prompt = (
        "Score this AI response from 0.0 to 1.0.\n"
        f"Task: {task.prompt}\n"
        f"Criteria:\n{criteria}\n"
        f"Response: {response[:2000]}\n"
        "Reply with ONLY a number 0.0-1.0."
    )

    import os
    import re as _re

    # --- Try Ollama first (free, local) ---
    # Note: NO "options" dict. Passing num_predict/temperature to gemma4:e4b
    # causes it to return empty strings on some Ollama builds. Gemma stops
    # naturally after emitting the score (usually 2-4 tokens).
    try:
        import requests
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        ollama_model = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        r = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=60,
        )
        if r.status_code == 200:
            raw = r.json().get("message", {}).get("content", "").strip()
            m = _re.search(r"\d*\.?\d+", raw)
            if m:
                return max(0.0, min(1.0, float(m.group())))
            print(f"[judge/ollama] no number in response: {raw!r}")
        else:
            print(f"[judge/ollama] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[judge/ollama] error: {e} -- falling back to Claude")

    # --- Fallback: Claude Haiku ---
    try:
        from anthropic import Anthropic
        client = Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        m = _re.search(r"\d*\.?\d+", raw)
        return max(0.0, min(1.0, float(m.group()))) if m else 0.0
    except Exception as e:
        print(f"[judge/claude-fallback] error: {e}")
        return 0.0
async def _run_task_async(task, brain):
    t0 = time.time()
    try:
        response = await asyncio.wait_for(brain.think(task.prompt), timeout=task.timeout_s)
        text = response.content if hasattr(response, "content") else str(response)
        tools_used = getattr(response, "tools_used", None)
        if task.expected_tool:
            if tools_used is None:
                print(
                    f"[benchmark] WARNING: task '{task.name}' expects tool "
                    f"'{task.expected_tool}', but response has no tool telemetry."
                )
            elif task.expected_tool not in tools_used:
                print(
                    f"[benchmark] WARNING: task '{task.name}' expected tool "
                    f"'{task.expected_tool}', but used tools={tools_used}"
                )
        latency = (time.time() - t0) * 1000
        quality = judge_response(task, text)
        return TaskResult(task.name, task.category, True, quality, latency,
                          response_text=(text or "")[:500])
    except asyncio.TimeoutError:
        return TaskResult(task.name, task.category, False, 0.0,
                          task.timeout_s * 1000, error="timeout")
    except Exception as e:
        return TaskResult(task.name, task.category, False, 0.0,
                          (time.time() - t0) * 1000,
                          error=f"{type(e).__name__}: {e}")


def run_task(task, brain):
    return asyncio.run(_run_task_async(task, brain))


def run_benchmark_suite(time_budget_s=180, hard_timeout_s=360):
    print(f"[benchmark] starting suite ({len(BENCHMARK_TASKS)} tasks)")
    t_start = time.time()

    if TanishiBrain is None:
        raise CrashStorm(f"TanishiBrain not importable: {BRAIN_IMPORT_ERROR}")

    try:
        brain = TanishiBrain()
    except Exception as e:
        traceback.print_exc()
        raise CrashStorm(f"TanishiBrain() init failed: {e}")

    results = []
    for i, task in enumerate(BENCHMARK_TASKS, 1):
        elapsed = time.time() - t_start
        if elapsed > hard_timeout_s:
            print(f"[benchmark] hard timeout at task {i}")
            break
        print(f"[benchmark] task {i}/{len(BENCHMARK_TASKS)}: {task.name}")
        r = run_task(task, brain)
        results.append(r)
        status = "OK" if r.success else f"FAIL ({r.error})"
        print(f"[benchmark]   -> {status}  q={r.quality_score:.2f}  {r.latency_ms:.0f}ms")

    total_time = time.time() - t_start
    if not results:
        return BenchmarkResult(0.0, 0.0, 0.0, [], total_time)

    quality = sum(r.quality_score for r in results) / len(results)
    latency = sum(r.latency_ms for r in results) / len(results)
    reliability = sum(1 for r in results if r.success) / len(results)
    print(f"[benchmark] done in {total_time:.1f}s  q={quality:.3f}  lat={latency:.0f}ms  rel={reliability:.3f}")
    return BenchmarkResult(quality, latency, reliability, results, total_time)
