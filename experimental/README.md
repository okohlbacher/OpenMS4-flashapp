# Pinned runtime migration

The app consumes one pyOpenMS wheel, one relocatable native runtime archive, and
an independently built Vue bundle. The primary Dockerfile performs no OpenMS or
Vue compilation. Archived upstream Dockerfiles and deployment scripts are
migration references, not inputs to this image.

## Artifact contract

Create `artifacts.lock.json` from `artifacts.lock.example.json` using actual build
outputs. The schema is version 2. Core and pyOpenMS source commits must equal the
app's `dependencies.lock.json`; the runtime also records exact OpenMSCLI,
OpenMSTOPP and OpenMSFLASH versions and commits. The standalone FLASHTnT package
is pinned under `dependencies.FLASHTnT` in the app dependency lock, so coordinated
package updates include it. Runtime provenance retains `external_tools.FLASHTnT`
as its serialized field; the verifier compares it to that normal dependency pin.

The wheel embeds `pyopenms/_build_provenance.json`:

```json
{"schema_version":1,"source_revision":"<pyOpenMS commit>","source_dirty":false,
 "version":"<pyOpenMS version>","core":{"...":"complete Core build JSON"}}
```

The runtime contains `share/openms4/runtime-provenance.json`:

```json
{"schema_version":1,"core":{"...":"complete Core build JSON"},
 "packages":{"OpenMS":{"source_revision":"<commit>","version":"4.0.0"},
             "OpenMSCLI":{"source_revision":"<commit>","version":"1.0.0"},
             "OpenMSTOPP":{"source_revision":"<commit>","version":"1.0.0"},
             "OpenMSFLASH":{"source_revision":"<commit>","version":"1.0.0"}},
 "external_tools":{"FLASHTnT":{"repository":"<source URL>","source_revision":"<commit>",
                               "version":"<reported version>","source_dirty":false}},
 "executables":["FLASHDeconv","DecoyDatabase","FLASHTnT"]}
```

Copy `build_identity` from the Core metadata fields listed in the verifier's
`CORE_FIELDS`. These check exact artifact selection, including build configuration.
The test-support flag is recorded identity, not an assertion that enabling tests
itself changes the ABI. The runtime and wheel must embed the same complete Core
JSON, including compiler, standard library, dual ABI/debug/runtime flags, features
and dependency versions/targets. Source metadata and hashes identify the trusted
producer's outputs; they do not independently prove that arbitrary binary bytes
were compiled from the claimed source. Native smoke tests remain required.

Place only the two named files under `artifacts/`. The archive must contain
`bin/FLASHDeconv`, `bin/DecoyDatabase`, `bin/FLASHTnT`, shared libraries, runtime
data and installed tool registries. Executables must be regular native files with
execute permission on Unix. The verifier checks ELF/Mach-O/PE architecture, wheel
distribution metadata, embedded source/build identity and SHA-256 digests before
extraction. It rejects traversal, special files, duplicate members and writes
through archive links; normal relative library aliases are supported. Universal
Mach-O archives are currently unsupported and need a target-specific bundle;
single-architecture fat containers retained by wheel repair are supported.

```bash
python experimental/verify_artifacts.py artifacts.lock.json artifacts --check-host
export PYTHON_IMAGE='python:3.12-slim@sha256:<actual-image-digest>'
docker compose build
docker compose up
```

The build fails on missing or incompatible artifacts. It installs the ordinary
Python dependency lock with hash checks and installs only the verified local
pyOpenMS wheel. Compose runs local workflows and persists `/workspaces`.

## Published pyOpenMS wheel acceptance

The released split pyOpenMS wheel can be tested independently of runtime assembly.
The committed `pyopenms-linux-x64.lock.json` and
`pyopenms-macos-arm64.lock.json` record the ci.5 release wheel SHA-256, exact Core
and pyOpenMS source pins, and complete Core build identity. The Linux wheel requires
glibc 2.39 or newer; the ARM macOS wheel requires macOS 26. Both require CPython 3.12.

For example, on Linux x86_64 with Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
mkdir -p wheel-artifacts
gh release download pyopenms-v4.0.0.dev0-ci.5 --repo okohlbacher/OpenMS4-pyopenms \
  --pattern pyopenms-4.0.0.dev0-cp312-cp312-manylinux_2_39_x86_64.whl --dir wheel-artifacts
.venv/bin/python experimental/verify_artifacts.py experimental/pyopenms-linux-x64.lock.json \
  wheel-artifacts --wheel-only --check-host
.venv/bin/python -m pip install --no-deps wheel-artifacts/*.whl
.venv/bin/python -m pip check
.venv/bin/python -m pip install pytest==9.1.1 fakeredis==2.38.0
.venv/bin/python -m pytest tests -v
```

Use the macOS lock and `pyopenms-4.0.0.dev0-cp312-cp312-macosx_26_0_arm64.whl`
on ARM macOS. The artifact directory must contain only the selected wheel.
`--wheel-only` verifies hashes, source pins, native architecture and build metadata,
and cannot extract a runtime. Default verification and Docker assembly still require
the complete runtime including independently pinned FLASHTnT; passing app tests does
not qualify native workflows or the whole image. The Linux app CI job
runs this wheel acceptance check on each push and pull request with unchanged
hash-pinned app requirements.

## Standalone FLASHTnT boundary

The original public app Dockerfile used `https://github.com/t0mdavid-m/OpenMS.git`,
branch `FVdeploy`. On 2026-09-10 GitHub returned source commit
`3f508829ad81c91354d397966f28d428e29e5329`; its
[FLASHTnT source](https://github.com/t0mdavid-m/OpenMS/blob/3f508829ad81c91354d397966f28d428e29e5329/src/topp/FLASHTnT.cpp)
is present (blob `80628a74e452c5110a98358f78becabbddaa53d1`). Its standalone port is
now [OpenMS4-flashtnt](https://github.com/okohlbacher/OpenMS4-flashtnt), version 1.0.0,
with the exact commit in `dependencies.lock.json`. It builds against the installed
split SDK; private algorithms remain in the tool package. Linux native tests and
the actual app TagWorkflow now run using its installed executable and the released
pyOpenMS wheel. The retained May 2025 results differ numerically from this port of
the March 2026 upstream source, even with all saved parameters applied. No complete
old default-parameter snapshot is retained, so historical numerical equivalence
remains unqualified. Full-image acceptance still requires a matching verified
runtime archive and completed scientific validation.
FLASHQuant views uploaded results here; it is not a newly invented executable.

The native acceptance harness uses the bundled AQPZ example, runs the actual app
parsers and checks tag sequences, row counts, scores, masses, fragment coverage and
the matched protein sequence against the retained outputs. It writes its report
before returning a failure if those historical values differ. For installed SDK
tools on `PATH`, run each mode into a new directory:

```bash
python experimental/accept_flashtnt.py --output /tmp/aqpz-tagging
python experimental/accept_flashtnt.py --raw-workflow --output /tmp/aqpz-workflow
```

The full workflow includes FLASHDeconv. The saved settings are ion types b/c/y/z,
minimum tag length 4, FLASHDeconv tolerances 5/5 ppm and maximum charge 30. Other
scientific options use defaults generated by the pinned native tools.

The original [2026-09-12 AQPZ report](validation/flashtnt-aqpz.json) records the
installed FLASHTnT build at `250debb`: tagging plus app parsing took 11.154 seconds;
the full raw workflow took 13.223 seconds. Both completed and identified the
same full AQPZ sequence, but produced 622 tags and score 559 versus the retained
2968 tags and score 505. The report retains the differing mass, fragment count
and coverage values. The acceptance command deliberately returns failure for
these historical comparison differences; successful execution does not establish
scientific equivalence. These short smoke-test timings are not a benchmark suite.
Subsequent AddressSanitizer checks found two native out-of-bounds reads in that
`250debb` version, so its scientific results are not qualified. The dependency lock
now requires `4ca4e73`, which incorporates both fixes; the earlier report remains
as historical evidence and must not be used as a new scientific reference.

## Online execution and acceptance

For online execution, run a separately managed Redis server and RQ worker using
the identical app image, settings, and shared workspace volume/path. Set the same
`REDIS_URL` on the Streamlit and worker containers. Start the worker with
`rq worker openms-workflows --url "$REDIS_URL"`. The primary image does not start
Redis, nginx, cron, or an embedded supervisor. The archived entrypoint scripts
assume the old upstream image and must not be used with this recipe.

Local workers acknowledge ownership registration over a pipe before starting tools;
a retained process join reaps completed children across Streamlit reruns. This avoids
the discarded-semaphore race exposed by the actual image UI.

The UI and worker use the same execution-mode/thread settings. Nonzero commands
raise; worker `False`, `None` and exception outcomes are failures. Job lookup
transport errors retain `.job_id`. The caller-generated job ID is persisted before
enqueue; a lost acknowledgement never starts a duplicate local workflow. Cancellation verifies PID creation identity,
checks owned descendants, and waits for RQ acknowledgement; failure preserves
recoverable records instead of declaring success.

Queued starts serialize the saved job identity check and enqueue under a Redis
submission lock. Only a confirmed terminal job with no remaining process records
permits a rerun. A missing or expired saved job is unavailable, rather than proof
that execution ended: retain `.job_id` until an operator independently confirms
the job and owned children have stopped. The 30-second submission lease is renewed
after lookup; UI Redis calls use a three-second timeout without retries. Queue
outages never switch an online submission to local execution.

A hard-killed RQ work-horse can still be interrupted in the short interval between
starting a tool and recording its identity. The marker and descendant checks
reduce this window but are not an orphan-proof supervisor. Live Redis/RQ
interleaving tests and full app workflows remain release gates. Legacy raw PID
records are deliberately not signalled because their ownership cannot be verified;
remove them only after independently confirming the old process has exited.

## Validation

The [live queue acceptance](validation/live-queue.json) now exercises a real RQ
SpawnWorker against an isolated Redis instance: controlled command success,
failure, and cancellation with an owned child and descendant. It exposed a race
where the worker returned its cancellation before RQ's stop message arrived;
the worker result now preserves that cancellation so the UI displays Cancelled.
This check covers macOS SpawnWorker, not Linux fork-worker or transport-loss cases.
Reproduce against a dedicated, empty Redis database (the harness rejects a
nonempty database):

```bash
docker run --rm --detach --name flashapp-queue-test --publish 127.0.0.1:16387:6379 \
  redis@sha256:becdda6c7f4b3fb42e42fd7f120bbf5c54c4caaaf16f26da24e4563d2c1f0576 \
  redis-server --save '' --appendonly no
REDIS_URL=redis://127.0.0.1:16387/0 python experimental/accept_live_queue.py --output /tmp/flashapp-queue-test
docker stop flashapp-queue-test
```

Use a new output directory for each run. The script stops its worker on exit;
the caller stops Redis. Full image and scientific workflow gates remain separate.

The concurrent queued-start test can also use a dedicated empty Redis database:
`FLASHAPP_TEST_REDIS_URL=redis://127.0.0.1:16387/0 python -m pytest tests/test_queued_starts.py -q`.
At `2eaad619`, all 14 focused tests passed in 0.19 seconds, including exactly one
enqueue for concurrent starts and rejection after a real expired submission lease.

## Linux runtime assembly

`assemble_linux_runtime.py` consumes an already-tested native graph build, its
clean source checkouts and original conda license cache. It selects the three
app executables and their resolved non-system ELF libraries, copies runtime data
and tool registries, checks the trusted builder records against the app pins and
verifies the complete runtime/wheel contract. Run it on the Linux builder:

```bash
python experimental/assemble_linux_runtime.py --work /scratch/build/work \
  --source /scratch/build/source --dependencies /scratch/dependencies \
  --wheel /scratch/wheels/pyopenms-4.0.0.dev0-cp312-cp312-manylinux_2_39_x86_64.whl \
  --wheel-lock experimental/pyopenms-linux-x64.lock.json --output /scratch/flashapp-inputs
```

Use a new output directory. The resulting `artifacts.lock.json` and `artifacts/`
are image inputs. Loading uses the image's `/opt/openms4/lib` search path; native
execution must still be tested in isolation from the SDK and conda prefixes.
This assembly record trusts the builder and does not prove arbitrary object files
were produced from the claimed source. A Python 3.12 base must also meet the
wheel's glibc minimum; Debian bookworm is too old for the released Linux wheel.
If GCC runtime recipes retain only their installed exception text, supply
`--gcc-license /path/to/original/COPYING3` from the trusted dependency cache.
The receipt records the license source paths and hashes; absent license texts
stop assembly. Core source must also be clean and match the installed SDK. Original
notices for statically embedded/header-only Core components and Percolator are
copied from that source; Eigen is included from its exact-version conda recipe
even though it has no dynamic library in the loader closure.

The committed Vue bundle was rebuilt from the exact submodule commit using its
unchanged npm lock. [Build evidence](validation/vue-build.json) records its source,
tool versions and output hashes. Production build and type checking passed; the
upstream checkout contains no unit tests. No frontend dependency upgrade was made.

```bash
python -m unittest discover -s tests -p test_artifacts.py -v
python -m pytest tests/test_execution_lifecycle.py tests/test_queue_manager_cancel.py tests/test_log_status.py -v
```

These isolated tests need pytest, psutil, rq, redis and fakeredis; they need no
OpenMS build or pyOpenMS import. Artifact fixtures contain small native headers
and exercise rejection paths; they are not executable scientific binaries.
Lifecycle tests launch only controlled Python children. Real `spawn` tests cover
local ownership registration, delayed bootstrap, failed startup and process reaping;
the earlier owner-ordering test also uses `fork` where available. Full UI/scientific tests additionally require
the actual verified wheel/runtime and app requirements. App dependency resolution,
image assembly, native ABI loading, live queue cancellation, and scientific result
correctness are distinct acceptance checks; passing one does not imply the others.

The app's unit workflow runs isolated contracts and ordinary-dependency data tests. The manual artifact
workflow downloads an explicitly selected `flashapp-artifacts` bundle and applies
the same verifier. The old monolithic release workflow is archived at
`experimental/build-and-test.upstream.yml`; its old scheduled GHCR deletion policy
is likewise archived in `experimental/ghcr-cleanup.upstream.yml`. No replacement image publication or
production rollout is claimed.

The ordinary Python lock was resolved with pip-compile 7.6.1 on Python 3.12,
macOS arm64. Its 55 distributions installed successfully with `--require-hashes
--only-binary=:all:` and `pip check` passed. Imports and a pandas→Arrow parquet→
Polars round trip passed with NumPy 2.5.3, pandas 2.2.3, Polars 1.44.2, PyArrow
19.0.1 and SciPy 1.18.1. The existing upload/compression/selection/legal helpers
and Compose input tests also passed. Linux wheel installation is a CI gate;
the released pyOpenMS wheel has a separate acceptance check above.
