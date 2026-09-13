# pyOpenMS ci.3 acceptance — 2026-09-13

The five-platform release `pyopenms-v4.0.0.dev0-ci.3` packages source
`b7edae7a89d902727e0cd50ff488fefc60e011e0` with Core
`bc9cc12514c768385ce121d6ca4bb710fe1983c4`.
Source CI run 34720130408 and publication run 34756250759 succeeded.
All twenty published artifacts were checked against the source CI outputs:
native archive, wheel and both checksums for each platform.

On ARM macOS with CPython 3.12, installing the locked ci.3 wheel without
replacing the application dependencies and running `python -m pytest -q tests`
passed 136 tests and 52 subtests in 39.91 seconds, with one skip and twelve
warnings. This checks the application against the new Python artifact; image
and native scientific acceptance are separate checks.
