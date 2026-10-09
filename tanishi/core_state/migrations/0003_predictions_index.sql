-- 0003: an index for the prediction ledger (node CS5, red team R7).
-- Never edit this file once a real database exists: add a new migration instead (decision 0007).
-- Do not put a semicolon in a comment: migrate() splits this file on them.
--
-- tool_forecast() runs on every tool call and reads the last resolved rows of one `about`, newest first.
-- Without this index that is a full scan plus a sort. With it, a seek on `about` and a walk down resolved_at.

CREATE INDEX predictions_about_resolved ON predictions(about, resolved_at);
