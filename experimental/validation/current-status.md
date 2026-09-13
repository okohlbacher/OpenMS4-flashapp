# Current FLASHApp acceptance status — 2026-09-13

This remains an experimental port. Software execution, packaging and scientific
agreement are separate checks. No image has been published or deployed.

| Check | Current evidence |
| --- | --- |
| Published split Python wheel | ci.2 wheel hashes and embedded source/Core metadata verified on Linux x86_64 and macOS arm64; [record](pyopenms-ci2.md). |
| Complete app regression suite | 136 tests and 52 subtests passed in 2.57 seconds at `b551a67`, with one opt-in live Redis skip. |
| Frozen ordinary Python dependencies | All 55 distributions installed with hashes; `pip check` and data interchange checks passed. |
| Vue production bundle | Exact submodule source and unchanged npm lock; type checking and build passed in 4.77 seconds. Upstream has no unit tests; [record](vue-build.json). |
| Live queue lifecycle | Real Redis/RQ SpawnWorker success, failure and owned-descendant cancellation passed on macOS; [record](live-queue.json). |
| Concurrent queued submissions | 14 focused tests passed against real Redis in 0.19 seconds, including a single enqueue and rejection after an expired submission lease. |
| Linux runtime assembly | Three executable dependency closures, licenses, runtime data and exact native pins assembled and artifact-verified; final image requalification is pending the memory-safe FLASHTnT build. |
| Image loader and page controls | Intermediate isolated image imported the wheel, loaded all three tools, generated their parameters and rendered both workflow configuration/run controls. This is not final native scientific qualification. |
| Local workflow startup | Browser acceptance exposed an actual semaphore bootstrap failure. `25f422e` uses a pipe acknowledgement and a process reaper; real spawn tests cover delayed bootstrap, registration/acknowledgement failure and zombie-free completion. |
| FLASHTnT scientific comparison | Earlier executable `250debb` completed the workflow but had two out-of-bounds reads discovered by sanitizers. Its [old result](flashtnt-aqpz.json) is unsafe historical evidence, not a reference. The app now requires `4ca4e73`; final app/image comparison is pending. |

The remaining acceptance work is the exact final image and native workflow rerun,
including successful local UI execution and browser-side result rendering. The
retained May 2025 AQPZ outputs are not a complete same-source/defaults baseline
for the March 2026 upstream algorithm. Numerical agreement remains unqualified;
passing execution or sanitizer checks does not establish historical equivalence.

The live queue checks cover macOS SpawnWorker, not every Linux fork-worker or
transport-loss interleaving. A hard-kill between child creation and ownership
recording remains a narrow orphan-risk interval. Missing saved Redis jobs require
explicit reconciliation, not automatic job-ID removal or local fallback.

The assembly receipts trust the controlled builder records and clean source
checkouts; they do not cryptographically prove arbitrary binary source claims.
The Linux image needs glibc 2.39 or later and Python 3.12. Native Windows/macOS app
distributions, deployment and complete frontend test coverage are not qualified.
