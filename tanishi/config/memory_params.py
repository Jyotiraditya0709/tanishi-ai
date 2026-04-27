"""Memory retrieval params. Mutated by autoresearch."""

SIMILARITY_THRESHOLD = 0.75   # min cosine similarity to retrieve a memory
MEMORY_TOP_K = 7              # how many memories to retrieve per query
MEMORY_RECENCY_BIAS = 0.1     # weight on recency when ranking
