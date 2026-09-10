# Pinned runtime migration

This app snapshot is independent from the OpenMS source split. Its primary Dockerfile consumes a verified pyOpenMS wheel and a relocatable runtime bundle, and no longer clones or compiles a mutable OpenMS branch. The former Dockerfiles are preserved here only as migration references.

Before building an image, create `artifacts.lock.json` from the example with actual artifact SHA-256 values and the core source commit. Put the named wheel and runtime archive in `artifacts/`; provide `PYTHON_IMAGE` pinned by digest. The runtime archive must include core/CLI shared libraries, versioned data, tool manifests and executables. FLASHTnT is a separate runtime requirement of this public app snapshot and does not exist in the audited OpenMS source; supply a separately pinned artifact or disable that workflow in a subsequent app change. FLASHQuant is a viewer for uploaded results here, not a newly invented executable.

No artifact hashes or binary compatibility are fabricated. Until verified artifacts exist, the image recipe intentionally fails. Requirements other than pyOpenMS retain the upstream pins; their compatibility with pyOpenMS 4 and the Vue component must be tested. This recipe is experimental developer deployment, not a production security/packaging claim.
