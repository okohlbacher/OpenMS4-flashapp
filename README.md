# FLASHApp

FLASHApp for visualizing FLASHDeconv's results \
This app is based on [OpenMS streamlit template project](https://github.com/OpenMS/streamlit-template).

![Overview](https://github.com/user-attachments/assets/35fe2c24-7dbc-40cd-b8b5-b7504768ade1)

run locally:

`streamlit run app.py local`

### Working with submodules

This project uses a git submodule to integrate openms-streamlit-vue-component.

When checking out this repository you will need to run a few extra commands:

`git submodule init`

`git submodule update`

If you would like to update the submodule to the latest commit, use the following:

`git submodule update --init --recursive`

See [https://git-scm.com/book/en/v2/Git-Tools-Submodules](https://git-scm.com/book/en/v2/Git-Tools-Submodules)
for more documentation on submodules

## Build

To build FLASHApp, you first need to build the `openms-streamlit-vue-component`
and copy the build from `./openms-streamlit-vue-component/dist` to 
`./js-component/dist`.

You then should set streamlit to production in two locations:

* in `./.streamlit/config.toml` set `developmentMode` to `false`
* in `./src/components.py` set `_RELEASE` to `True`

These steps should be done before building any version of FLASHApp.

### Docker

This experiment consumes a separately built pyOpenMS wheel, native runtime bundle,
and the existing Vue bundle. It requires Python 3.12 and an immutable base-image digest.
It does not build OpenMS or fetch private sources during the image build.

Follow [the artifact contract](experimental/README.md), supply `artifacts.lock.json`
and the named files under `artifacts/`, then run:

```bash
export PYTHON_IMAGE='python:3.12-slim@sha256:<actual-image-digest>'
docker compose build
docker compose up
```

Open `http://localhost:8501`. Workspaces persist in a named volume. Compose runs
local workflows; online execution requires Redis and RQ workers with the same
image and shared workspace path, as described in the artifact guide.

A complete image still requires an assembled, verified native runtime archive.
The separately pinned FLASHTnT package now builds against Core and runs the native
Linux app workflow; numerical equivalence to the retained example remains unqualified.
The released pyOpenMS wheel can be installed and tested with the frozen app
dependencies now; follow the [wheel acceptance instructions](experimental/README.md#published-pyopenms-wheel-acceptance).

## Legal pages (Impressum, Privacy Policy, Terms of Use)

Every page shows **Impressum**, **Privacy Policy** and **Terms of Use** links in the
sidebar footer, and the GDPR consent banner links to the privacy policy. By default
these point to the official OpenMS pages (`https://openms.de/impressum`, `/privacy`,
`/terms`). To override them — for example when self-hosting or deploying FLASHApp
under a different operator — set `legal_links` in `settings.json`:

    "legal_links": {
        "impressum": "https://your-domain.example/impressum",
        "privacy": "https://your-domain.example/privacy",
        "terms": "https://your-domain.example/terms"
    }

Any link you omit falls back to its OpenMS default. The `privacy` URL is reused for the
consent banner's privacy-policy link, so consent and policy stay in sync.

<!-- package-graph:begin -->
## Where this package sits

![OpenMS 4 package architecture](docs/package-architecture.svg)

`flashapp` builds against the installed **core**, **cli**, **topp**, **flash**, **pyopenms**, **flashtnt** packages at the revisions recorded in [`dependencies.lock.json`](dependencies.lock.json). No other package builds against it.

| Repository | Relation | Contents |
| --- | --- | --- |
| [OpenMS4-core](https://github.com/okohlbacher/OpenMS4-core) | dependency | scientific library, OpenSwathAlgo, readers and writers, runtime data, optional TestSupport |
| [OpenMS4-cli](https://github.com/okohlbacher/OpenMS4-cli) | dependency | TOPPBase, tool registration and discovery |
| [OpenMS4-topp](https://github.com/okohlbacher/OpenMS4-topp) | dependency | 123 console tools |
| [OpenMS4-flash](https://github.com/okohlbacher/OpenMS4-flash) | dependency | FLASHDeconv and the OpenMS::FLASH backend |
| [OpenMS4-pyopenms](https://github.com/okohlbacher/OpenMS4-pyopenms) | dependency | nanobind bindings, installed module tree and repaired wheels |
| [OpenMS4-flashtnt](https://github.com/okohlbacher/OpenMS4-flashtnt) | dependency | FLASHTnT tagging executable |

The eighteen repositories are assembled by the parent repository
[OpenMS4-tests](https://github.com/okohlbacher/OpenMS4-tests), which holds the submodule pins (`packages.lock.json`), the
dependency-order build runner and the contract tests that keep the graph consistent.
[`docs/project-state.md`](https://github.com/okohlbacher/OpenMS4-tests/blob/main/docs/project-state.md) is the current state
of the whole project; [`docs/build-split-packages.md`](https://github.com/okohlbacher/OpenMS4-tests/blob/main/docs/build-split-packages.md)
reproduces the installed-SDK build.
<!-- package-graph:end -->
