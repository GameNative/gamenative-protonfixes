# Winetricks verb tooling

`tools/protonfixes/verbs.py` sorts every winetricks verb in `data/verbs.json` into one class. The app runs
each class differently:

| class | what the app does |
|---|---|
| `component` | Turns on the wincomponent the app already ships (`direct3d`, `xaudio`, `directsound`, `directmusic`, `directplay`, `directshow`, `vcrun2010`, `openal`, cnc-ddraw, core fonts) and merges the verb's DLL overrides. DirectPlay and DirectMusic also get one-time `regsvr32` steps. If the component is not available, the app falls back to `preferred`/`files`. |
| `registry` | Writes the Windows version, the audio driver (`setting.audioDriver`), or registry values and removals. |
| `download` | Downloads official files with a pinned sha256 and unpacks them into the prefix. |
| `offline_installer` | Downloads the official installer and lays out its files and registry values from `data/verbs/<verb>.layout.json`. Nothing runs under Wine. |
| `exec` | Puts the official installer into the prefix and queues an interpreter `exec` step. The step runs once per prefix, with a watchdog. |
| `skip` | Not applied. `reason` gives the cause. |

The `kind` field keeps its old meaning, so older app builds still work.

## Files

- `msi_tables.py`: builds the layouts. It downloads each `offline_installer` verb's installers, opens WiX bundles,
  self-extracting cabinets, zips and 7z files, and reads the MSI tables with a pure-Python OLE2 reader. It then
  writes `data/verbs/<verb>.layout.json`. With `--update-verbs`, it also records each layout's path and
  sha256 in `verbs.json`. To unpack LZX cabinets, it uses `bsdtar` (libarchive, the same reader the app uses),
  `cabextract` or `7z`. Stored and MSZIP cabinets need no extra tool.
- `pins.py` and `pins.json`: sha256 pins for download URLs that winetricks gives without a hash. URLs listed under
  `rolling` change their content in place. `--refresh-rolling` hashes them again.
- `tests/`: unit tests. They need no network.

```sh
python3 -I -m unittest discover tools/verbs/tests
python3 -I -m unittest discover tools/protonfixes/tests
python3 -I tools/verbs/pins.py --verbs data/verbs.json
python3 -I tools/verbs/msi_tables.py --verbs data/verbs.json --out data/verbs \
  --cache /tmp/verb-downloads --update-verbs
```

## CI

`.github/workflows/protonfixes-sync.yml` installs `libarchive-tools` and `cabextract`, runs `pins.py` (and the
converter again if `pins.json` changed), then `msi_tables.py --update-verbs` with a download cache. The job
timeout is 60 minutes; the installers come to about 2.5 GB. A changed `pins.json` is committed to `main`.

If a layout cannot be built (a download fails, or its hash changed upstream), `msi_tables.py` prints the verb and
keeps that verb's previous layout file. The run log shows which verbs have no layout.
