# Pinned runtime migration

The app consumes one pyOpenMS wheel, one relocatable native runtime archive, and
an independently built Vue bundle. The primary Dockerfile performs no OpenMS or
Vue compilation. Archived upstream Dockerfiles and deployment scripts are
migration references, not inputs to this image.

## Artifact contract

Create `artifacts.lock.json` from `artifacts.lock.example.json` using actual build
outputs. The schema is version 2. Core and pyOpenMS source commits must equal the
app's `dependencies.lock.json`; the runtime also records exact OpenMSCLI,
OpenMSTOPP and OpenMSFLASH versions and commits. Add an `external_tools.FLASHTnT`
entry to the app dependency lock only when its independently verified source,
version and repository are known for the supplied binary.

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

The released split pyOpenMS wheel can now be tested independently of the unresolved
FLASHTnT runtime. The committed `pyopenms-linux-x64.lock.json` and
`pyopenms-macos-arm64.lock.json` record the ci.2 release wheel SHA-256, exact Core
and pyOpenMS source pins, and complete Core build identity. The Linux wheel requires
glibc 2.39 or newer; the ARM macOS wheel requires macOS 26. Both require CPython 3.12.

For example, on Linux x86_64 with Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
mkdir -p wheel-artifacts
gh release download pyopenms-v4.0.0.dev0-ci.2 --repo okohlbacher/OpenMS4-pyopenms \
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
not qualify the missing native workflows or the whole image. The Linux app CI job
runs this wheel acceptance check on each push and pull request with unchanged
hash-pinned app requirements.

## External FLASHTnT boundary

The original public app Dockerfile used `https://github.com/t0mdavid-m/OpenMS.git`,
branch `FVdeploy`. On 2026-09-10 GitHub returned source commit
`3f508829ad81c91354d397966f28d428e29e5329`; its
[FLASHTnT source](https://github.com/t0mdavid-m/OpenMS/blob/3f508829ad81c91354d397966f28d428e29e5329/src/topp/FLASHTnT.cpp)
is present (blob `80628a74e452c5110a98358f78becabbddaa53d1`). That source identification
is not an OpenMS4 binary compatibility result. No compatible FLASHTnT artifact or
reported release version has been fabricated. Full-image and TagWorkflow
acceptance remain blocked until that dependency is supplied and tested.
FLASHQuant views uploaded results here; it is not a newly invented executable.

## Online execution and acceptance

For online execution, run a separately managed Redis server and RQ worker using
the identical app image, settings, and shared workspace volume/path. Set the same
`REDIS_URL` on the Streamlit and worker containers. Start the worker with
`rq worker openms-workflows --url "$REDIS_URL"`. The primary image does not start
Redis, nginx, cron, or an embedded supervisor. The archived entrypoint scripts
assume the old upstream image and must not be used with this recipe.

The UI and worker use the same execution-mode/thread settings. Nonzero commands
raise; worker `False`, `None` and exception outcomes are failures. Job lookup
transport errors retain `.job_id`. The caller-generated job ID is persisted before
enqueue; a lost acknowledgement never starts a duplicate local workflow. Cancellation verifies PID creation identity,
checks owned descendants, and waits for RQ acknowledgement; failure preserves
recoverable records instead of declaring success.

A hard-killed RQ work-horse can still be interrupted in the short interval between
starting a tool and recording its identity. The marker and descendant checks
reduce this window but are not an orphan-proof supervisor. Live Redis/RQ
interleaving tests and full app workflows remain release gates. Legacy raw PID
records are deliberately not signalled because their ownership cannot be verified;
remove them only after independently confirming the old process has exited.

## Validation

```bash
python -m unittest discover -s tests -p test_artifacts.py -v
python -m pytest tests/test_execution_lifecycle.py tests/test_queue_manager_cancel.py tests/test_log_status.py -v
```

These isolated tests need pytest, psutil, rq, redis and fakeredis; they need no
OpenMS build or pyOpenMS import. Artifact fixtures contain small native headers
and exercise rejection paths; they are not executable scientific binaries.
Lifecycle tests launch only controlled Python children. The local owner-ordering
test uses `fork` where available. Full UI/scientific tests additionally require
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
