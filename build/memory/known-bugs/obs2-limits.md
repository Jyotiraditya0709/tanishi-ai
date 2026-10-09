# OBS2 v0: known limits

- **Anyone who can emit events can log Human Effort.** `effort_start` and `effort_stop` with actor `human` are ordinary
  events, so any code that can call `emit()` can make up hours. When the Warden (W1) lands, it should restrict those kinds
  to the CLI path.
- **A forgotten timer runs forever.** A session that was never stopped is shown as running but does not count. Once it is
  stopped days later, all of that time counts. There is no idle cap.
- **`compute()` reads every `experiments` row** and parses each ts in Python (OBS3 writes `isoformat()` without fixed
  microseconds, so a string compare could be wrong). That is fine at v0 scale and slow at millions of rows.
- **B trusts `capabilities.state`.** Nothing checks that "mastered" was really verified by Reality, because the Reality
  Engine does not exist yet.
- **Five CEI factors, CAR, AR and IA are `None`.** See open-problems/OBS2.md.
