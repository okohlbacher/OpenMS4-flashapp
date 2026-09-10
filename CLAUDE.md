# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**FLASHApp** is a Streamlit web application for visualizing **top-down proteomics** results from the OpenMS FLASH* tool family. It is built on the [OpenMS streamlit-template](https://github.com/OpenMS/streamlit-template) and bundles three independent sub-applications, each registered as a section in `app.py`:

- **FLASHDeconv** (⚡️) — spectral deconvolution: raw MS ion peaks → neutral monoisotopic masses (proteoforms), with isotope/charge resolution and FDR scoring.
- **FLASHTnT** (🧨) — tag-and-track top-down identification: runs FLASHDeconv, then matches short sequence "tags" against a protein FASTA database to identify proteins (PrSMs), with target/decoy FDR.
- **FLASHQuant** (📊) — proteoform quantification from FLASHDeconv mass traces (view-only; no run step).

The heavy lifting is done by **TOPP command-line tools** (`FLASHDeconv`, `FLASHTnT`, `DecoyDatabase`) required by the Docker image; the app drives them, parses their output into pandas DataFrames, caches them per workspace, and renders them through a custom **Vue.js Streamlit component** (`flash_viewer_grid`).

## Commands and build boundary

Use Python 3.12. See `experimental/README.md` for exact artifact and dependency pins.
The primary Dockerfile consumes prebuilt OpenMS/pyOpenMS and Vue artifacts; it
has no compiler, Miniforge, GitHub token, architecture-specific build stages,
Redis server, or nginx. `docker/entrypoint.sh` and the archived Dockerfiles are
upstream migration references and are not used by this recipe.

```bash
python -m streamlit run app.py local
python -m unittest discover -s tests -p test_artifacts.py -v
python -m pytest tests/test_execution_lifecycle.py tests/test_queue_manager_cancel.py -v
```

The isolated tests require pytest, psutil, rq, redis and fakeredis. The remaining
UI/scientific tests additionally require the verified pyOpenMS wheel and complete
app requirements. Never substitute the public pyOpenMS wheel for the pinned build.
A set `REDIS_URL` selects online mode, regardless of `online_deployment` in settings.

Before building an image, verify the pinned Vue submodule and committed
`js-component/dist/`, `src/render/components.py` `_RELEASE=True`, and the
Streamlit production setting. Rebuild Vue separately from its pinned checkout
when needed; the app Dockerfile does not rebuild it. Do not update submodules to
mutable heads as part of an ordinary checkout.

## Architecture

### The data pipeline (the core mental model)

Every sub-app follows the same path; understanding it requires reading `src/Workflow.py`, `src/parse/`, `src/workflow/FileManager.py`, and `src/render/`:

```
mzML upload ──► WorkflowManager.execution()
                   └─ executor.run_topp('FLASHDeconv' / 'FLASHTnT' / 'DecoyDatabase')
                        └─ writes out_deconv.mzML, annotated.mzML, *.tsv, *.feature, *.msalign
                            └─ src/parse/* turns those into pandas DataFrames
                                └─ FileManager.store_data(dataset_id, key, df)  ──► workspace cache
                                                                                       │
Viewer page ◄── render_grid() (src/render/render.py) ◄── reads cached dfs + layout ◄──┘
                   └─ get_component_function()  ──► Vue `flash_viewer_grid` component
```

- **Workflows:** `src/Workflow.py` defines `DeconvWorkflow`, `TagWorkflow` (FLASHTnT), and `QuantWorkflow`, all subclasses of `WorkflowManager` (`src/workflow/WorkflowManager.py`). Each implements `upload()`, `configure()`, `execution()`, `results()`. `execution()` runs TOPP tools, stores every output file via `FileManager.store_file()`, then calls the parsers. `TagWorkflow` chains `DecoyDatabase` → `FLASHDeconv` → `FLASHTnT`.
- **Parsers (`src/parse/`):** `deconv.py::parseDeconv`, `tnt.py::parseTnT`, `quant.py::parseQuant` / `flashquant.py::parseFLASHQuantOutput` are the entry points. The mzML→DataFrame heavy lifting lives in `masstable.py` (`parseFLASHDeconvOutput`, `parseFLASHTaggerOutput`) using a **multiprocessing pool**; `tag_resolution.py` maps tags ↔ proteoforms.
- **Renderers (`src/render/`):** `render.py` (`render_grid`, `render_component`) pushes state into the Vue component and reads user selections back out. `components.py` declares the component and defines the per-cell component classes: `PlotlyHeatmap`, `Tabulator` (Scan/Mass/Protein/Tag tables), `PlotlyLineplot`, `Plotly3Dplot`, `FDRPlotly`, `SequenceView`, `InternalFragmentMap`, `FLASHQuant`. `compression.py` compresses payloads sent to the browser; `StateTracker.py` tracks selection state across cells (cross-component linking via shared scan/mass identifiers).

### Pages and the Layout Manager

Each sub-app's pages (in `content/<SubApp>/`) are registered in `app.py`. The distinctive FLASHApp concept is the **Layout Manager** (`FLASHDeconvLayoutManager.py`, `FLASHTnTLayoutManager.py`): a grid editor where the user composes which visualization components appear in which cells (≤5 experiments, ≤3 columns/row). The chosen layout is persisted to the workspace cache and the **Viewer** page renders it (falling back to a built-in default if none is saved).

- Layout is stored via `FileManager.store_data` under dataset key **`'layout'`** for FLASHDeconv and **`'flashtnt_layout'`** for FLASHTnT (separate namespaces — they share the underlying `deconv_dfs`/`anno_dfs` data but keep independent layouts). It is JSON-importable/exportable.
- **Sequence Input** (`FLASHDeconvSequenceInput.py`) saves a proteoform sequence + fixed modifications to the `'sequence'` dataset; doing so unlocks the `Sequence view` and `Internal fragment map` components in the Layout Manager.
- **FLASHQuant** is simpler: File Upload + a single fixed-layout Viewer, no Layout Manager, and uses a separate cache subdirectory.

### Workspaces & FileManager

State lives in per-session **workspaces** (`enable_workspaces: true`, `workspaces_dir: ".."` → `../workspaces-FLASHApp/`). `FileManager` (`src/workflow/FileManager.py`) is the single gateway to the workspace's `cache/`: `store_file`, `store_data`, `get_results`, `result_exists`, `get_results_list`, `get_files`, keyed by a `dataset_id` (typically `<filename>_<timestamp>`). Demo workspaces are seeded from `example-data/workspaces/` (`demo_workspaces` in `settings.json`).

### Parameters

`ParameterManager` (`src/workflow/ParameterManager.py`) persists widget state to JSON and generates TOPP `.ini` files. `configure()` exposes TOPP tool parameters via `self.ui.input_TOPP('FLASHDeconv', exclude_parameters=[...], custom_defaults={...})`. **Widget keys must match keys in `default-parameters.json`.** `presets.json` holds named parameter bundles (`test_parameter_presets.py` guards this).

### Deployment & runtime (`entrypoint.sh`, `k8s/`)

The container entrypoint starts **Redis** + one or more **RQ workers** (queue `openms-workflows`) + Streamlit. When `STREAMLIT_SERVER_COUNT > 1`, it runs N Streamlit instances behind an **nginx** load balancer with sticky-cookie session routing. `QueueManager` (`src/workflow/QueueManager.py`) offloads `execution()` to RQ when `online_deployment` is set. The entrypoint is written to tolerate **Apptainer/Singularity** read-only rootfs (all runtime state goes under `$RUNTIME_DIR`, default `/tmp/opendiakiosk`). `k8s/` is a kustomize base + `overlays/prod` deploying to namespace `openms` as `ghcr.io/openms/flashapp:latest` behind nginx/traefik ingress; `clean-up-workspaces.py` runs via cron for periodic GC.

### CI (`.github/workflows/`)

- `build-and-test.yml` — manual verification of an explicitly supplied `flashapp-artifacts` bundle from a workflow run. It does not build or publish images. The original upstream build/deployment workflow is archived at `experimental/build-and-test.upstream.yml`.
- `unit-tests.yml` — the pytest suite. `pylint.yml` — lint. `build-windows-executable-app.yaml` + `test-win-exe-w-embed-py.yaml` — the PyInstaller desktop build. `ghcr-cleanup.yml` — registry GC.

## Conventions & gotchas

- **`app.py` sets multiprocessing start method to `spawn`** (polars + Unix fork are incompatible) and imports `pyopenms` early (required for the Windows build). Don't remove these.
- **Running workflows locally needs the TOPP binaries** (`FLASHDeconv`, `FLASHTnT`) on `PATH` — they must be supplied by the verified runtime artifact. The upload/viewer/download paths work on pre-computed result files without them.
- **Windows packaged build:** `run_app.py` + `run_app_temp.spec` (PyInstaller). A `windows` arg in `sys.argv` triggers a working-directory `chdir` in `page_setup()`.
- Pages start with `page_setup()` from `src/common/common.py`, which initializes the workspace, sidebar, and params; call `save_params(params)` at the end. Use `show_fig()` / `show_table()` for consistent display.
- Decorate `configure()` / page sections with `@st.fragment` for partial reruns.
- Workflow display names map to lowercase-hyphenated keys ("FLASHTnT" → workflow dir / preset keys); TOPP params use colon paths (`tag:min_length`).
