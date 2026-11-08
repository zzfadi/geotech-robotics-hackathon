# Jetson runtime recovery evidence — 2026-11-08

This is the exact historical recovery recipe and its verification evidence,
not a general installer. The resumed installation passed on Ubuntu 24.04 ARM64
with Python 3.12.15 at repository commit
`d5c4f8d6ca67b4d3cc1a6eeb4eb22a1fce704542`. It installed the pinned SDK and ran
offline checks; it did not start a controller, configure CAN, or operate motors.

| File | Purpose | SHA-256 |
|---|---|---|
| [resume.py](resume.py) | Exact reviewed recovery script | `c698f4a6488a9b0e080644498f6fb0ae7e1115bb90ebd68ee8b7dffa3c38ec23` |
| [DEPENDENCY-CLOSURE.json](DEPENDENCY-CLOSURE.json) | Local inspection of cached wheel/SDK metadata for the target environment | `5cc507748e654fa77df6e7a96f7658a4b3fbb4007e113b262f13b8945be6495b` |
| [PROVISIONING-RESULT.json](PROVISIONING-RESULT.json) | Result copied from the installed Jetson | `a1261d1d52d79cf5af35a29d6f145f0330d0a706c64c0eb71152317cca88c2da` |

The result hash and execution evidence are also recorded under
`runtime_installation` in [the installation record](../jetson-usb-prep-2026-11-08.json).
That record reports 140 passing offline tests, 18 compatible installed packages,
and no missing system libraries. The dependency-closure report is metadata
analysis; the target result records the completed installation and checks.

## What the recovery fixed

The initial build hash lock omitted `packaging`, required by `wheel 0.48.0`.
The corrected build closure is setuptools 84.2.1, wheel 0.48.0 and packaging 26.3.
The script checks the complete cached dependency graph before installing offline.
It also permits regenerated CPython `__pycache__/*.pyc` contents while retaining
file-type checks and hash verification of source files and native binaries.

## Preconditions and reuse limits

- Run only on the installed Ubuntu 24.04 ARM64 host as the expected non-root
  account (`fadi` by default), with system Python 3.12 or later. The live installer
  is rejected.
- The existing user-owned `~/jetson-runtime`, its `.venv`, and
  `~/geotech-robotics-hackathon` must already exist. This recipe does not create
  them, clone the repository, or replace the operating system.
- The clean checkout must be on `main`, have the recorded GitHub origin, and
  descend from `119d38be9a91feae23fdfc1d4d39ede8fd7a697a`. The reviewed
  `arm/tools` tree is `d2d77d0685048494972cca98673a8dce23f1e204`; the requirements
  blob is `3cb6c49eda024baf6f2418d56f07d39f2843df4a`.
- **The later Linux camera helper changes that tools tree. Rejection on newer
  code is intentional. Never update the pins merely to bypass this check.**
  Review new code and record a separate recipe/result for a later deployment.
- The external `jetson-runtime-ready.tar.gz`, its extracted manifests, wheels,
  SDK source and interpreter archives are required. They are not included here.
  Its SHA-256 is `ab7c3c02e743e07442394dd916dd9fb3605ebc22809ffe6797a6cea53ee59c85`.
  See [cache provenance](../jetson-prep-2026-11-07.json) and the installation
  record's `runtime_cache` and `project_bundle` entries. The preparation bundle
  hash is `0f04f5c09baddd5089bb41c35aca78f929f088529a1da7bf425cd4d720503db4`.
- The explicit `--apply` flag permits environment installation. It is not a
  read-only validation mode. A rerun replaces the known build lock and overwrites
  `DEPENDENCY-CLOSURE-VERIFIED.json` and `PROVISIONING-RESULT.json` in the runtime
  directory. Preserve prior results before any reviewed reuse.

The historical invocation was:

```sh
python3 resume.py --assets "$HOME/jetson-prep-assets" --apply
```

Keep this snapshot unchanged. These reviewed text files contain package/source
identifiers and the already documented local account/path, but no credentials,
cloud account details, Keychain helpers, raw logs, images, or binaries.
