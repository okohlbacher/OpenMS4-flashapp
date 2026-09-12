# Published pyOpenMS consumer acceptance — 2026-09-12

The full FLASHApp test suite passed against the released CPython 3.12 ARM macOS
wheel: **109 tests and 49 subcases, 5.19 seconds**, without changing any app
dependency version. The environment used Python 3.12.14 on ARM macOS 26, the
unchanged `requirements.txt` installed with `--require-hashes --only-binary=:all:`,
pytest 9.1.1 and fakeredis 2.38.0. `pip check` passed. The test process had
`OPENMS_DATA_PATH`, `PYTHONPATH`, `DYLD_LIBRARY_PATH` and `LD_LIBRARY_PATH` unset.

The exact wheel and Core identities are recorded in
[`../pyopenms-macos-arm64.lock.json`](../pyopenms-macos-arm64.lock.json).
The Linux x86_64 wheel also passed digest, source, complete build identity and
native-architecture verification against
[`../pyopenms-linux-x64.lock.json`](../pyopenms-linux-x64.lock.json).
Linux execution is tested by the app's updated GitHub workflow; a macOS header
inspection of its Linux wheel is not a Linux execution result.

This acceptance exposed two issues that smaller isolated suites missed:

- The repaired macOS wheel includes `libgcc_s.1.1.dylib` in a single-slice Mach-O
  fat container. The verifier now accepts that form only when its contained
  native header agrees with its architecture declaration. Multiple architectures
  and malformed slices remain rejected.
- `test_parameter_presets.py` removed all shared `src.workflow` modules during
  collection, causing a lifecycle test's patch to target a different module
  from the one being tested. It now loads a private copy of ParameterManager
  with mocked Streamlit. The full suite verifies the combined collection order.

The new wheel-only verification mode checks one wheel using the same source,
digest, architecture and build identity checks as full image verification. It
rejects runtime archives and runtime extraction. The default mode still requires
all app executables and independently pinned FLASHTnT provenance.

Six existing deprecation warnings remain: one from the controlled fork test,
four from RQ's `job.exc_info`, and one from Polars' `pl.count()`. The first
sandboxed run could not inspect test-created descendant processes; final tests
ran with that OS permission. These results cover app helpers, scientific imports
and numerical checks, artifact contracts and controlled workflow/queue tests.
They do not establish a full Streamlit deployment, live Redis worker acceptance,
or successful native FLASHTnT execution.
