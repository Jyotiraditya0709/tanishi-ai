# Open problems

One file per problem or per wrong spec (`<node>.md`). Seeded:

- **Local tool use.** The local model path has no tools. Which local model on a 16 GB MacBook can call tools reliably enough for the router (SUB3) to use it for personal steps?
- **One shared brain.** `TanishiBrain` is a single global object. The Cognitive Substrate (SUB1, SUB2) should give each task its own state; how does the legacy API keep working during the switch?
- **Continuity questions.** Who writes the 50 sealed Continuity Test questions, and how are they refreshed without leaking into memory?
- **Measuring Max plan usage.** The orchestrator only learns about the limit when a run fails. Is there a reliable signal earlier?
