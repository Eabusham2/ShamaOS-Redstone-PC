# Connected runtime deadline review

The historical wrapper `faccd37e13eab70a7eb8dd577501073b7f283e88ab10d081cc914f28100309d2` checked an already-reaped child before checking its wall limit. A child that completed after the deadline between polls could retain exit code zero and be accepted.

The corrected monitor records elapsed time before accepting completion, preserves the real `wait4` status, and marks every observation at or beyond its wall limit incomplete. It uses `os.kill` for the owned child so subprocess polling cannot consume the exit status or resource accounting first. Both live callers and retained safe/negative clock verification reject late execution explicitly.

`review.json` binds the source and 43 passing deterministic tests. `native-review.json` binds the same small saved-world fixture, runner, scenario and historical output used by the committed `independent_review/native-review.json` proof of the parent wrapper `faccd37`. The earlier `c985ff` wrapper appears only in that proof’s historical chain; this comparison is directly against `faccd37`. All 1,433,600 trace samples, 5,408 assertions and snapshot bytes match. `check.py` reproduces that comparison from the retained baseline report. Raw native traces stay outside the repository.

This is a process-boundary correction. It provides no full-cache or whole-machine runtime acceptance. The original wrapper evidence remains retained under its original identity; the running native generation and its frozen source bundle were not changed. New runtime preparation must bind the corrected helper sources.
