# FLASHApp validation record — 2026-09-10

The final isolated suite passed **72 tests and 46 subcases**. Results are in
[test-results.txt](test-results.txt). Coverage uses coverage.py 7.16.0 with both
thread and multiprocessing tracing; parent and controlled local-workflow traces
were combined. It measures complete selected files, including their inherited
untouched code, rather than claiming every modified line is covered.

| File | Statements | Branches |
| --- | ---: | ---: |
| `experimental/verify_artifacts.py` | 221/246 (89.8%) | 128/154 (83.1%) |
| `src/workflow/CommandExecutor.py` | 129/206 (62.6%) | 39/92 (42.4%) |
| `src/workflow/QueueManager.py` | 111/180 (61.7%) | 18/36 (50.0%) |
| `src/workflow/WorkflowManager.py` | 84/144 (58.3%) | 14/38 (36.8%) |
| `src/workflow/_processes.py` | 57/88 (64.8%) | 21/28 (75.0%) |
| `src/workflow/_settings.py` | 8/8 (100.0%) | 0/0 |
| `src/workflow/tasks.py` | 72/86 (83.7%) | 11/16 (68.8%) |

Exact missing statements and branches are in [coverage.txt](coverage.txt) and
[coverage.json](coverage.json). Overall combined statement/branch coverage is
69%. Principal uncovered areas are the existing `run_topp` parameter construction
(which needs pyOpenMS), GUI constructors/delegates, real Redis initialization and
queue statistics, uncommon permission/kill-timeout cleanup branches, optional
worker progress/error-log failures, and some invalid-metadata/host-check branches.
This is useful regression coverage, not complete workflow or platform coverage.

## What ran

- Commands spawned real controlled Python children; spawn and registration
  failures, bounded stderr tails, EOF drain, stdout/stderr reader errors,
  descendant cancellation, creation-time mismatch and missing scripts were tested.
- A real local workflow fork waited for its ownership record, ran a child and
  cleaned up. This test uses `fork` only where available; the emitted Python
  multi-threaded-fork warning is retained, not suppressed.
- Queue tests use fakeredis. They verify stopped acknowledgement, lost stop
  replies, preserved job IDs during lookup outages, and lost enqueue replies both
  before and after the actual fake-Redis enqueue. No live Redis/RQ worker is claimed.
- The actual TagWorkflow execution method was exercised in isolation to confirm
  failed DecoyDatabase execution prevents later tools/result storage. Both actual
  workflow methods reject missing inputs. Scientific parsing was not substituted
  with claimed end-to-end success.
- Worker reconstruction with `False`, `None`, exception and `True` outcomes was
  exercised without a GUI. Existing exception result display remains intact.
- The canonical deployed artifact verifier used small ZIP/tar fixtures, native
  ELF/Mach-O/PE headers and negative provenance/path/hash cases. Real installed
  Core JSON for commit `4fdec46b205459b92e7d3b9e56df5d8e912d5c85` also passed in
  synthetic archive/wheel containers; see [installed-core-provenance.json](installed-core-provenance.json).
  These are metadata/parser checks, not executable OpenMS binary validation.
- All 55 ordinary app distributions installed from `requirements.txt` with
  `--require-hashes --only-binary=:all:` in an isolated Python 3.12 environment on
  Darwin arm64. `pip check` passed. Imports and pandas/Arrow/Polars parquet exchange
  passed, along with existing upload/compression/selection/legal helper tests.
- Compose accepted an explicit image input and rejected a missing one. These
  checks parsed configuration only; they did not pull images, build or start Docker.

## Remaining acceptance gates

The compatible FLASHTnT binary/version and separately pinned external provenance
are still unavailable. The public source fork is identified in the artifact guide.
The actual pyOpenMS4 wheel, assembled runtime, Linux image, full UI/scientific
workflows, Windows/native macOS distribution and live Redis/RQ worker lifecycle
are not established by these app tests.

There remains a narrow hard-kill interval after a queued worker starts a tool but
before it records the PID identity. The cancellation marker, verified descendants
and RQ acknowledgement improve normal recovery, but do not prove this interval
orphan-free. It remains a live-queue acceptance gate. Raw legacy PID records are
retained and never signalled without a creation identity.

## Reproduce

From the app checkout, install the ordinary hash lock and the isolated test
packages (pytest 9.1.1, fakeredis 2.38.0, coverage 7.16.0). Run:

```bash
python -m coverage erase --rcfile=experimental/validation/coverage.rc
python -m coverage run --rcfile=experimental/validation/coverage.rc -m pytest \
  tests/test_execution_lifecycle.py tests/test_artifacts.py \
  tests/test_queue_manager_cancel.py tests/test_log_status.py \
  tests/test_dependency_smoke.py tests/test_manual_upload.py \
  tests/test_render_compression.py tests/test_selection_clear.py \
  tests/test_legal_links.py tests/test_deployment_contract.py
python -m coverage combine --rcfile=experimental/validation/coverage.rc
python -m coverage report --rcfile=experimental/validation/coverage.rc
```

macOS process enumeration must be available for psutil's owned-descendant checks.
The first sandboxed test run exposed and fixed an `OSError` cleanup path; final
controlled process tests ran with permission to inspect the test-created processes.
