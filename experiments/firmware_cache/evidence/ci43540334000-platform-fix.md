# Published CI platform failures and focused correction

This evidence concerns GitHub Actions run **43540334000** at exact published
commit **55449200dc2513b114ec988938ab58c50c8a4dd5**. It does not claim CI coverage
for subsequent local snapshot optimizations or their provenance bridge.

Run: <https://github.com/Eabusham2/ShamaOS-Redstone-PC/actions/runs/43540334000>.

## Observed failures

All four platform jobs completed with the following exact scopes:

| Platform | Failed | Passed | Dependency skips | Failure scope |
| --- | ---: | ---: | ---: | --- |
| Ubuntu 24.04 / Python 3.11 | 3 | 1,125 | 647 | Two AST receipt checks; stale spec heading |
| Ubuntu 24.04 / Python 3.12 | 1 | 1,127 | 647 | Stale spec heading |
| Windows / Python 3.11 | 3 | 1,125 | 647 | Two AST receipt checks; stale spec heading |
| Windows / Python 3.12 | 1 | 1,127 | 647 | Stale spec heading |

The original platform failure summaries and job IDs are retained in
`ci43540334000-platform-failures.json`. No result has been replaced by the local
passing checks. At 01:35:39 UTC, all 12 physical partitions, RTL, and world-backend
jobs had succeeded. Final partition reconnection and the behavioral job were
still active; this file does not attest their final outcomes.

## Causes and correction

The canonical specification already records the owner-authorized cache reduction
under **November 13, 2026 (America/Chicago)**. The spec-lock test still expected
the previous UTC-formatted heading. The correction makes the assertion match
the existing intentional documentation. Cache capacity, execution window,
application features, and the specification itself are unchanged.

The provenance bridge pins exact method AST hashes produced by Python 3.12.
Python 3.11 represents the same method without an empty `type_params` field, so
its `ast.dump` bytes differ despite identical source and behavior. The old
method's raw digest on the Python 3.11 field layout is
`807d812f591f7164e9505b5d03f8b6a9eb90a3ed304465d2cef8c046ed32bc71`, instead of the
pinned Python 3.12 representation below.

Commit **792866d** adds `reviewed_ast_dump`, which deep-copies the AST, includes
the missing empty generic-parameter field on the applicable declaration nodes,
and preserves all existing field values. Python 3.13 also changes dump defaults
to hide empty lists; the helper explicitly includes them on that runtime.

Both existing pinned receipts stay unchanged:

- Retained fingerprint:
  `e39a12a195b0a0989ea5d4e928a32efa912245194e4fda1fbfab69824f405bda`.
- Optimized fingerprint:
  `66a27bf9520986c21c1645b300d4d4fd659c75896edf9f968d52725a1fbb7bec`.

The helper retains nonempty generic parameters even if an attribute is attached
without appearing in `_fields`. Tests verify both such cases are rejected and
that normalization does not mutate the input AST. Existing rejection of changed
fingerprint code and unrelated native writer code remains enforced. No broader
method allow-list or production behavior change is introduced.

## Local verification

A separate worktree was created directly at published commit `5544920`, before
applying the correction. The original focused run reproduced **one failure and
four passes** on Python 3.12.14, matching the remote 3.12 failure scope. The
original log and JUnit remain `ci43540334000-before.txt` and `.xml`.

After the patch, **33 focused tests passed with zero skips/failures/errors in
0.844 seconds** on Python 3.12.14:

```sh
PYTHONPATH=src:tests pytest \
  tests/test_cache_provenance_bridge.py tests/test_spec_lock.py \
  tests/test_apron_recovery.py tests/test_generator_cache_provenance.py \
  --junitxml=build/ci554-fixed-focused.xml
```

The corrected JUnit and stdout are retained as
`ci43540334000-fixed-focused.xml` and `.txt`. These include exact Python 3.11
AST-field-shape simulation for both retained and optimized methods. An actual
Python 3.11 interpreter was not available for this local check; renewed CI must
confirm the actual 3.11 runtime result.

Actual Python 3.13 independently reproduced both unchanged pinned digests and
rejected modified methods. Its version, output, and audited helper-source hash
are retained in `ci43540334000-python313.txt`. This is a focused standard-library
AST check, not a full Python 3.13 application suite.

The followup was committed locally for integration. No remote write, workflow
rerun, cancellation, or automation was performed by this audit.
