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
  self-extracting cabinets and 7z archives, zips and 7z files, and reads the MSI tables with a pure-Python OLE2
  reader. It then writes `data/verbs/<verb>.layout.json`. With `--update-verbs`, it also records each layout's path
  and sha256 (and its `requiresTools` and `dictSizeBytes`) in `verbs.json`. It unpacks each archive with `bsdtar`
  (libarchive, the same reader the app uses) when it can, else with `cabextract`, else with `7z`/`7zz`. Stored and
  MSZIP cabinets need no extra tool. Files that the .NET setup chainer copies into `SetupCache` from its own payload
  are not in the package cabinets; they are left out with a note.
- `ranges.py` and `ranges.json`: byte ranges for the DLLs that winetricks takes from Windows service packs (see
  "Byte-range files" in `SCHEMA_NOTES.md`). The service packs never change, so this runs by hand:
  `python3 -I tools/verbs/ranges.py --verbs data/verbs.json --cache /tmp/verb-downloads`. It needs `bsdtar` and
  `cabextract`; both must unpack each DLL from its slice with the same sha256.
- `device_tools.py`: writes `data/tools.json`, the Termux packages of `cabextract` and `7zz` that `tool` hops run
  (see "tools.json" in `SCHEMA_NOTES.md`). Run it again when Termux updates those packages.
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

## Layout files

`data/verbs/<verb>.layout.json` has `installers` (url, sha256, size, arch), `archives`, `files` (`archive` index,
`member`, `to`, `size`), `registry`, `unpackedBytes` and `notes`, and optionally `requiresTools` and
`dictSizeBytes`. Each archive is `{"installer": <index>, "hops": [...]}`, plus `"tool"` when its members must be
extracted with that tool. The app starts from the downloaded installer and applies each hop to the output of the
previous one:

| hop | meaning |
|---|---|
| `"a0"` (a string) | Extract that member with libarchive. |
| `{"slice": [offset, length]}` | Those bytes of the previous output (a cabinet or 7z archive attached to an executable). |
| `{"ranges": [[offset, length], ...], "size": n}` | Those byte ranges joined, cut to `size` (a cabinet stored as an MSI stream). |
| `{"type": "tool", "tool": "cabextract" \| "7zz", "member": "<path>"}` | Extract `member` from the previous output with that tool, e.g. `cabextract -d <dir> -F <member> <file>` or `7zz x -o<dir> <file> <member>`. A `7zz` hop has `dictSizeBytes`. |

After the last hop, each file's `member` is extracted from the archive with libarchive, or with the archive's `tool`
when it has one. The tool hops exist because libarchive cannot read some of the .NET archives: 7z archives with BCJ2
(the .NET Framework 4.x self-extractors) and WiX cabinets whose file entries share data (libarchive rejects a folder
whose files are not contiguous, "Invalid CAB header"). The layout's `requiresTools` lists every tool its hops and
archives use, and `dictSizeBytes` is the largest 7z dictionary among them.

## CI

`.github/workflows/protonfixes-sync.yml` installs `libarchive-tools`, `cabextract` and `7zip`, runs `pins.py` (and the
converter again if `pins.json` changed), then `msi_tables.py --update-verbs` with a download cache. The job
timeout is 60 minutes; the installers come to about 2.5 GB. A changed `pins.json` is committed to `main`.

If a layout cannot be built (a download fails, or its hash changed upstream), `msi_tables.py` prints the verb and
keeps that verb's previous layout file. The run log shows which verbs have no layout.
