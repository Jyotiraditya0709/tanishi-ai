# AR2 · Practice tier limits (2026-10-09)

Referenced from `tanishi/arena/practice/legacy.py` (`system_check`) and `checks.py`.

- [ ] **`get_system_info` does not report RAM.** The legacy `system_check` task ("how much RAM do I have?") cannot be
      answered from that tool alone, so the verifier accepts a real `get_system_info` *or* `run_command` call and checks
      the figure against the verifier process's own `os.sysconf` memory (within 10%). It does not check that the figure
      came from the tool output: an agent that calls `get_system_info` and guesses the right size scores 1. Fix: add a
      `memory_total` line to `get_system_info`, then require the answer to match the tool output like `os_python` does.
- [ ] **No retry in `ToolRegistry`.** The legacy `flaky_probe` assumed the registry retries; it does not. The first call
      fails and the agent must call again. The verifier only needs one successful real call, so it still works.
- [ ] **`aimed=` is a substring check on the tool input JSON.** `list_directory {"path": "nologs"}` counts as aimed at
      `logs`. Matters less since the AR2 repair: the file tasks also need the random planted value to come out of a
      tool's output (`evidence=`), which a call at the wrong path cannot give.
- [x] **Tool "success" is the registry's, not the tool's.** `read_file` on a missing path can return an error string
      with `success: true`. Since the AR2 repair every file task needs the planted value in the output (or, for
      `count_matching_files`, every planted file name or the count from a shell call), so an error string earns nothing.
      The write tasks rely on the file on disk.
- [ ] **Forged events.** Code in the attempt can call `emit()` itself and write a matching tool_call/tool_result pair
      (see `tooltrace.py`). Closed only when the Warden (W1) owns the log.
- [ ] **`tanishi.autoresearch.benchmark` loads the repo `.env` on import.** An executor that imports it to register
      the real flaky probe pulls API keys into the attempt process. The AR2 test executors copy the probe instead.
