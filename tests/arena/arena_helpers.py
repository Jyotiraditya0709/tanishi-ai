"""Shared constants and queries for the AR1 exam."""
CANDIDATE = "exam-config"


def experiments(conn):
    cur = conn.execute("SELECT id, candidate, task_set, seed, score, cost, meta FROM experiments ORDER BY seed, id")
    return cur.fetchall()
