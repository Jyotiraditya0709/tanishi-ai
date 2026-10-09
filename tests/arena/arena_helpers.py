"""Shared constants and queries for the AR1 exam."""
CANDIDATE = "exam-config"


def stub_executor(task, candidate, seed):
    """Smallest executor from the attempt contract (decision 0008): fixed, non-blank output. Picklable by module path."""
    return "stub output"


def experiments(conn):
    cur = conn.execute("SELECT id, candidate, task_set, seed, score, cost, meta FROM experiments ORDER BY seed, id")
    return cur.fetchall()
