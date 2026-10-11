# gamenative-protonfixes

Game fix data for [GameNative](https://github.com/utkarshdalal/GameNative), converted to JSON:

- [umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes) per-game fixes for every store
  (Steam, GOG, Epic, Humble, itch.io, Ubisoft, umu ids), with the umu-database id mapping.
- The [winetricks](https://github.com/Winetricks/winetricks) verbs those fixes use, classified by how the app runs
  them, with pinned download hashes and offline-installer layouts.
- The per-app tables in Valve's [Proton](https://github.com/ValveSoftware/Proton) `proton` script (`proton_11.0`).

The converters never import or run upstream code. They parse Python with `ast` and read shell scripts as text.
The app never runs Python and does not ship this data; it downloads the JSON at launch.

## Layout

| path | what |
|---|---|
| `tools/protonfixes/` | `convert.py` (umu-protonfixes + winetricks → JSON), `verbs.py` (verb classes), hand-written `overrides/`, tests. See its [README](tools/protonfixes/README.md). |
| `tools/valveproton/` | `convert.py` (Valve's `proton` per-app tables → JSON), tests. |
| `tools/verbs/` | `pins.py`/`pins.json` (sha256 pins for unhashed winetricks URLs), `msi_tables.py` (offline-installer layouts), `ranges.py`/`ranges.json` (byte ranges of service-pack DLLs), `device_tools.py` (`data/tools.json`), tests. See its [README](tools/verbs/README.md). |
| `data/` | The generated output (below). |

## The JSON contract

[`tools/protonfixes/SCHEMA_NOTES.md`](tools/protonfixes/SCHEMA_NOTES.md) describes the format. Every file has a
`schema` field; the app ignores any file with a schema newer than it reads (currently `1`).

`data/` contains:

- `index.json`: `entries` keyed by `"<store>/<id>"`, each with `file`, `sha256` and `coverage`, plus a
  `defaults` map with the store-wide default document for each store.
- `<store>/<id>.json`: one fix document per store id; `<store>/default.json`: the store default.
- `verbs.json`: the winetricks verb table.
- `verbs/<verb>.layout.json`: offline-installer layouts, referenced by path and sha256 from `verbs.json`.
- `tools.json`: the Termux aarch64 packages of `cabextract` and `7zz`, which some layouts need (`requiresTools`).
- `valve/index.json` and `valve/<appid>.json`: Valve's per-app tables.
- `REPORT.md`: coverage, unsupported steps, override state and verb classes. Read it after each run.

All paths in `index.json` and `verbs.json` are relative to the `data/` root. The app rejects a document whose
sha256 does not match the index.

## What the app expects

The app tries these sources in order and caches what it fetched under `filesDir/protonfixes/`:

1. `https://fixes.gamenative.app/protonfixes/latest.json`, which is `{"version":"<segment>","schema":1}`.
   `<segment>` is one path segment (`<YYYYMMDD>-<short sha>`). The data is then read from
   `https://fixes.gamenative.app/protonfixes/<version>/`, a copy of `data/` (`<version>/index.json`,
   `<version>/steam/<appid>.json`, `<version>/verbs.json`, `<version>/valve/...`, ...).
2. The `main` branch of this repo:
   `https://raw.githubusercontent.com/GameNative/gamenative-protonfixes/main/data/`.
3. The local cache.

A versioned folder is never changed after upload. `latest.json` is written last, so the app never sees a version
that is only partly uploaded.

## Pipeline

`.github/workflows/protonfixes-sync.yml` runs every Monday and on manual dispatch:

1. Installs `libarchive-tools`, `cabextract` and `7zip` (cabinets and 7z archives that libarchive cannot read).
2. Clones umu-protonfixes (full history, blobs filtered, for per-file commits) with its umu-database submodule,
   winetricks, and ValveSoftware/Proton `proton_11.0`.
3. Runs the three test suites.
4. Runs `tools/protonfixes/convert.py --out data`.
5. Runs `tools/verbs/pins.py` to pin new unhashed downloads; if `pins.json` changed, converts again.
6. Runs `tools/verbs/msi_tables.py --update-verbs` to build the offline-installer layouts (about 2.5 GB of
   downloads, cached between runs; the job timeout is 60 minutes). A layout that cannot be built keeps its
   previous file.
7. Runs `tools/valveproton/convert.py --out data/valve`.
8. Commits a changed `tools/verbs/pins.json` to `main`.
9. Commits the regenerated `data/` and any changed pins to `main`.
10. If the `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID` secrets are set, uploads `data/` to
    `s3://gamenative-protonfixes/protonfixes/<version>/` on R2 (served at `https://fixes.gamenative.app/protonfixes/`) and then writes `protonfixes/latest.json`.

`.github/workflows/ci.yml` runs the tests on every pull request.

## Run it locally

Python 3.11+, standard library only.

```sh
git clone --filter=blob:none https://github.com/Open-Wine-Components/umu-protonfixes /tmp/pf
git -C /tmp/pf submodule update --init --depth 1 subprojects/umu-database
git clone --depth 1 https://github.com/Winetricks/winetricks /tmp/winetricks
git clone --depth 1 --branch proton_11.0 https://github.com/ValveSoftware/Proton /tmp/proton

python3 -I -m unittest discover tools/protonfixes/tests
python3 -I -m unittest discover tools/valveproton/tests
python3 -I -m unittest discover tools/verbs/tests

python3 -I tools/protonfixes/convert.py --protonfixes /tmp/pf \
  --umu-database /tmp/pf/subprojects/umu-database/umu-database.csv \
  --winetricks /tmp/winetricks/src/winetricks --out data
python3 -I tools/verbs/pins.py --verbs data/verbs.json
python3 -I tools/verbs/msi_tables.py --verbs data/verbs.json --out data/verbs \
  --cache /tmp/verb-downloads --update-verbs
python3 -I tools/valveproton/convert.py --proton /tmp/proton/proton --branch proton_11.0 --out data/valve
```

The output is deterministic: a run on unchanged inputs gives an empty diff.
