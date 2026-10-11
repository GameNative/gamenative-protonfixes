# Schema notes (deviations and clarifications to spec v1)

This file lists every place where the generated data in `protonfixes/` differs from or adds to the shared spec
(`protonfixes-spec.md`, schema 1). Everything not listed here follows the spec as written.

## App-side requirements (from the interpreter author)

| Requirement | Status in the data |
|---|---|
| Every alias id has its own `index.json` entry | Yes. Each symlink, and each extra store codename, gets its own `<store>/<id>.json` file and its own index key. `aliases` and `canonical` are informational only. |
| Epic documents are keyed `egs/<Epic appName>` | Yes. Non-Steam ids are the store `CODENAME` from umu-database for `(UMU_ID, STORE)`. For `egs`, that column is the Epic/Legendary app name (often a 32-char hex string). Four fixes have no umu-database row for their store, so they fall back to the umu id (`umu-50620` for egs and gog, `umu-955050` for gog, `umu-12810` for zoomplatform). `REPORT.md` lists them. |
| Each `install` entry names only the arch that the file really is | Yes. A file entry is one archive member, and its `install` list only has that member's arch. x86 and x64 builds are always separate entries with different `entry` or `nested` values. Fonts use arch `any`. |
| `users/steamuser` paths become `${drive_c}/users/${user}/...` | Yes. This is done for every path, including `set_ini_options` with `BasePath.USER` / `APPDATA_LOCAL`. `${user}` is a new placeholder that the spec does not list. The pre-Vista folder names that upstream still uses are mapped to the folders that Wine creates (and that games get from `SHGetFolderPath`): `My Documents` → `Documents`, `Local Settings/Application Data` → `AppData/Local`, `Application Data` → `AppData/Roaming`. `BasePath.USER` is therefore `${drive_c}/users/${user}/Documents`. |
| `sync` overwrites, `registry` is write-if-missing | No conflict. Note that upstream `regedit_add` overwrites (`reg add /f`), so write-if-missing can keep an old value that the fix wanted to change. This matters most for `HKCU\Software\Wine\DllOverrides` values (e.g. steam/211420). |

## Document fields

- `umuId` (extra): added to every non-Steam document. It holds the umu id that upstream used for the file.
- `notes` (extra, optional): document-level notes from the converter, e.g. `upstream fix has no main()`.
- `upstreamCommit`: the short sha of the last upstream commit that touched the fix's real file (from `git log -1 -- <file>`).
  If git history is not available, this falls back to HEAD. This keeps documents unchanged between syncs when their
  fix did not change. In a shallow clone, every document gets HEAD.
- `upstream` on an alias document is the alias's own (symlink) path.
- `name`: comes from the umu-database title when one exists, otherwise from the first line of the fix's docstring.
- Stores: the spec lists `steam, gog, egs, umu, ubisoft, zoomplatform, humble, itchio`. Upstream also has
  `gamefixes-amazon`, `-battlenet` and `-ea` directories, but at this commit they only hold `__init__.py`, so no
  documents are made for them. `itchio` only has `default.py`.
- `override` (extra, optional): set when a hand-ported override replaced the converter output (see "Overrides"). It is
  the repo path of the override file.

## Store defaults (`<store>/default.json`)

Upstream runs `gamefixes-<store>/default.py` for every game of that store, before the game's own fix. Each one is
converted to `protonfixes/<store>/default.json` (stores at this commit: `steam`, `gog`, `egs`, `umu`, `itchio`).
`index.json` lists them in a new top-level `defaults` map: `{"<store>": {"file": "<store>/default.json", "sha256": ...}}`.
They are not in `entries`.

```json
{
  "schema": 1, "store": "steam", "upstream": "gamefixes-steam/default.py", "upstreamCommit": "6095dbb",
  "coverage": "full",
  "steps": [],
  "launchOptions": [
    {"name": "-pf_tricks", "step": {"op": "verb", "name": "{value}"}},
    {"name": "-pf_dxvk_set", "split": "=", "step": {"op": "dxvk_config", "set": {"{key}": "{value}"}}},
    {"name": "-pf_replace_cmd", "split": "=", "step": {"op": "replace_command", "from": "{key}", "to": "{value}", "regex": true}}
  ]
}
```

- `steps` run for every game of the store, before the game's document (same step format). For example, `egs`
  installs `vcrun2022` and creates `HKCR\com.epicgames.launcher`; `gog` sets `OPENSSL_ia32cap`.
- `launchOptions` describes the `-pf_*` arguments that upstream reads from the game's launch options. For each
  argument of the form `<name>=<param>` (split at the first `=`):
  1. Remove the argument from the command line (upstream always removes it, also when it is ignored).
  2. If `split` is set, split `param` at the first `split` character into `key` and `value`. If there is no `split`
     character, ignore the option. Without `split`, `value` is the whole `param`.
  3. Replace `{key}` and `{value}` in every string of `step` (keys too), then run the step like a document step.
- Options are applied in the order in which they appear on the command line.
- The app decides which values it accepts. For `-pf_tricks` it should only accept verbs that `verbs.json` supports
  (the converter does no allowlisting). `-pf_replace_cmd` is a raw regex from the user.
- An option whose body the converter does not recognise has an `unsupported` step and must be ignored (but still removed).

## Overrides (hand-ported fixes)

Some upstream fixes are written as imperative Python that the converter cannot follow. These are ported by hand in
`tools/protonfixes/overrides/<store>/<stem>.json`, keyed by the upstream file (`gamefixes-<store>/<stem>.py`). An
override applies to every document made from that file, aliases included. Format:

```json
{
  "upstream": "gamefixes-steam/3590.py",
  "upstreamSha256": "<sha256 of the upstream file the port was written against>",
  "comment": "what upstream does and how the port differs",
  "confidence": "high | medium: ...",
  "steps": [ ... ],
  "coverage": "full",
  "notes": ["optional notes copied to the document"]
}
```

- `steps` replace the converter output completely. They use the same step format as converted documents.
- `coverage` is optional; without it the converter computes it from the steps.
- If the upstream file changes, `upstreamSha256` no longer matches. The converter then uses its own output again and
  `REPORT.md` lists the override as `stale` until someone reviews it and updates the hash.
- Overrides are written for per-step evaluation of `when` (the app checks each step's conditions just before it runs
  that step). Some of them depend on it, e.g. steam/2229850 installs cnc-ddraw only when the `cncnet_ra2` verb
  step before it did not succeed.

## Steps

- `phase`: only `"early"` is ever emitted. Steps from `main()` / `main_with_id()` have no `phase`.
- `when` is AND-only, as in the spec. An upstream `A or B` becomes several copies of the step, one per disjunct
  (e.g. `[{env A=1}]` and `[{not env A=1}, {env B=1}]`). Every op is idempotent, so running a copy twice is safe.
- Extra predicates in `when`:
  - `{"verbInstalled": "<verb>"}`: true when the app has installed that verb in this container (upstream
    `util.checkinstalled`, which reads `winetricks.log`). `checkinstalled('gui')` is always false.
  - Host predicates with a boolean value, e.g. `{"wayland": true}` or `{"wayland": false}`. The converter emits them
    instead of the upstream environment checks:
    - `wayland`: `PROTON_USE_WAYLAND` / `PROTON_ENABLE_WAYLAND` set (or `== "1"`). Upstream always tests both with OR,
      which now collapses to one step.
    - `gamescope`: `GAMESCOPE_WAYLAND_DISPLAY` set, or `XDG_CURRENT_DESKTOP == "gamescope"`.
    - `nosteam`: `NOSTEAM` set (the game runs without the Steam client).
    - `intel_hybrid`: Intel hybrid CPU (12th to 15th gen); only used by the steam/1382330 override.
  - `env` with `equals` compares exactly. When upstream lower-cases the value first (`str.lower(os.getenv(...))`),
    only the lower-case spellings are emitted.
- Sequencing: the converter computes each guard from the upstream test, which upstream runs once, in program order.
  When a step changes what a later step's guard tests, and both steps sit under the same test, per-step evaluation
  would skip the later step. `REPORT.md` lists such cases under "Sequencing hazards" (there are none at this commit;
  the ones that existed are fixed in overrides or by the converter, e.g. a `touch` before a `write_file` of the same
  file is dropped).
- `unsupported` steps can carry an extra `step` field. This is the step that would have been emitted if its condition
  could be expressed. It is informational only, and the app must still skip the step.
  `source` can be `""` when the unsupported part is a condition and not a statement.
- Store conditions (`store` / `storeNot`) are never emitted. Each document is converted for one store and id, so
  store and id checks (`os.environ['STORE']`, `UMU_ID`, `SteamAppId`, `game_id == ...`) are resolved by the converter.
- Paths that are relative to the working directory in upstream (e.g. `open('player.ini')`, `os.path.exists('modorganizer2')`)
  are emitted relative to `${install}`. Proton's cwd is normally the game exe's directory. These are the same for most games,
  but not all.
- A side effect inside an assignment or a `return` (e.g. `retc = run([...]).returncode`) is reported as an
  `unsupported` step. Before, it was dropped without a trace (steam/452440 was reported `full` with no steps).
- A `when` literal `fileExists` is used for upstream `isfile`, `isdir` and `exists` alike.

### Op-specific

- `replace_command`: upstream is a case-insensitive regex substitution over every argv element.
  - Executable swap (`to` not empty), literal pattern: `from` is the plain file name of the exe to replace (compare it
    case-insensitively with the file name of the selected exe), and `to` is relative to that exe's directory. A
    pattern with directories is reduced to its file name and `to` gets one `../` per directory, e.g. steam/1361210
    `launcher/Launcher.exe` → `binaries/Darktide.exe` becomes `from: "Launcher.exe"`, `to: "../binaries/Darktide.exe"`.
    An escaped dot counts as literal (`Game\.exe` → `Game.exe`); any other regex syntax (anchors, classes,
    quantifiers, alternation) makes the step a regex step.
  - Anything else has `"regex": true`: `from` is the upstream (Python) regex and `to` the replacement. Apply it like
    upstream: a case-insensitive regex replace on the full exe path (with `/` separators) and on each argument.
    At this commit: steam/7510 (`launcher\.exe$`), steam/1795390 (`CarnageCross/CarnageCross`, no file name) and
    the `-pf_replace_cmd` launch option.
  - `to: ""` removes the matched argument (e.g. `{"from": "-issteam", "to": ""}`, only under `when: {"nosteam": true}`).
    The `${install}/<to>` existence check from the spec does not apply to this form.
  - When upstream passes non-default regex flags, the step has `note: "case-sensitive match upstream"`.
- `cwd` (new): `{"op": "cwd", "path": "${install}/hd"}`. Upstream `os.chdir()`: start the game with this working directory.
- `import_saves` (new): `{"op": "import_saves", "fromAppid": 2203230, "path": "Documents/My Games/X/${steam_id64}/SaveGames", "copy": true}`.
  Upstream `util.import_saves_folder(from_appid, relative_path, is_demo)`. `path` is relative to the user profile
  folder (`drive_c/users/<user>`) of both prefixes, with the legacy folder names mapped. Upstream behaviour: if the
  folder exists in the other game's prefix and not in this one, create the parent folders, then symlink the folder
  (`copy: false`) or copy it (`copy: true`, used for demo saves). If the folder exists here and `copy` is true, copy
  only the files and folders that do not exist here yet. If it is a broken symlink, re-create it.
- `runtime_app` (new): `{"op": "runtime_app", "appid": 1826330}` (Proton EasyAntiCheat Runtime) or `1161040` (Proton
  BattlEye Runtime). Upstream `install_eac_runtime()` / `install_battleye_runtime()`, which make Steam install that app.
- `text_replace` (new): `{"op": "text_replace", "path": ..., "from": ..., "to": ..., "regex": false}`. Replace every
  match of `from` in the file with `to`; do nothing if the file is missing. With `"regex": true`, `from` is a
  Java/Kotlin regex and `to` a Java replacement string (`$1` for groups). Literal `sed -i 's/a/b/' file` commands are
  converted to this (non-regex; `note` says that sed only replaces the first match on each line). Hand-ported
  regex edits are in steam/312060, 39210 and 409090.
- `xml` (new): `{"op": "xml", "path": ..., "xpath": "//runtime", "value": "<a x=\"1\" />\n<b />", "backup": true}`.
  Upstream `set_xml_options(anchor, xml_line, file)` inserts `xml_line` after every line that contains the opening tag
  `anchor`, once (it skips the file if `<file>.protonfixes.bak` exists). `xpath` is `//<tag>` from that anchor and
  `value` is one or more XML elements, one per line. Suggested app behaviour: for every element matching `xpath`,
  append each element of `value` as a child unless the element already has a child with the same tag name. Only
  steam/244850 uses it. `encoding` is set when it is not utf-8.
- `exec`: only steam/452440's override uses it, as defined by the verb work (`path` is a host path with placeholders,
  `args`, `once`).
- `dll_override`: the disabled value is always emitted as `"d"`, never `""`. Keys can have a `.exe` suffix
  (`wineexe_override`, e.g. `dxwebsetup.exe`) or a leading `*` (`*dsound`), exactly as upstream wrote them.
- `registry`:
  - `values` can be `{}`. This means "create the key" (upstream `regedit_add(key)` without a value).
  - The value name `""` is the key's default value (`@` in .reg files).
  - `REG_DWORD` values are decimal strings (`"0x1"` becomes `"1"`, `dword:00002b48` becomes `"11080"`).
  - `REG_BINARY` values are lowercase hex with no separators.
  - `HKEY_CLASSES_ROOT` is emitted as `HKLM` + `Software\Classes\...`.
  - Upstream writes with 64-bit `reg.exe`, which means the 64-bit view (keys that already contain `Wow6432Node` are kept as written).
  - MapleStory (steam/216150) writes a `.reg` file and runs `wine regedit` on it. The converter parses that file and turns it into `registry` steps.
- `ini`: extra fields:
  - `backup: true`: upstream copies the file to `<path>.protonfixes.bak` before the first edit, and does not edit the file
    if that backup already exists. So upstream applies the edit once per install. The app can follow this, or re-apply each launch.
  - `encoding` (only when it is not utf-8, e.g. `cp1251`).
  - `createIfMissing` is always `false`, because upstream does nothing when the file is missing.
  - One step is emitted per INI section. `section` is `null` only for keys before any section header.
- `write_file`: `encoding` can also be any Python codec name that upstream used. `ifMissing: true` is set when upstream
  checks that the file does not exist before it writes.
- `append_line`: always has `ifAbsent: true`. Upstream either checks for the line, or guards the append with a marker file.
  The second case keeps its guard in `when`.
- `json_set`: `key` is a top-level key (no dotted path is ever emitted). The step has a note when upstream only sets
  the key if it is already present.
- `file`:
  - Single-path actions (`mkdir`, `touch`, `delete`, `chmod_ro`) put the path in `to`. `from` is only used by `copy`, `move` and `symlink`.
  - For `symlink`, `from` is the link target and `to` is the link path. `from` can be relative to the link's directory (as `ln -s` does).
  - `recursive: true` is set for `shutil.rmtree`, `shutil.copytree` and `cp -r`.
  - `ifMissing` is never emitted. That condition is in `when` instead.
  - In a document that also has an unsupported step, `delete`/`move` steps that come before it become `unsupported`. This
    prevents half-applied destructive fixes, e.g. deleting the Desktop folder without making the link that replaces it.
  - `glob: true` (only on `delete`): the last path component of `to` is a glob pattern (`*`, `?`, `[...]`;
    case-sensitive, not recursive, as upstream `Path(dir).glob(pattern)`). Delete every regular file in that folder
    whose name matches; directories are never deleted. Do nothing when the folder is missing.
  - `minSizeBytes: N` (only with `glob`): delete a matched file only if its size is at least `N` bytes. Upstream
    `size // 1000 // 1000 > 10` becomes `11000000`. Used by steam/950670 to remove huge log files.
- `download`:
  - `backup` paths are relative to `to`.
  - `sha256` is `null` when upstream does not check the archive.
  - The step can have a `note` when upstream skipped the download because the target files already had the patched checksums.
    The app should apply a download once per container, e.g. with a marker keyed by url+sha256.
- `dos_device`: `path` can be a host path (`/tmp` is the upstream default for `create_dos_device()`). In that case the app must choose a suitable directory.
- `cpu_topology`: always has `nosmt`. `set_cpu_topology_limit(n)` is `{"limit": n, "nosmt": false}`: use `n`
  logical cores, but only if the CPU has more than `n`. `set_cpu_topology_nosmt()` is `{"nosmt": true}`: use one
  logical core per physical core when SMT is on (upstream ignores its `core_limit` argument because of a `max()`
  bug, so no `limit` is emitted). `set_cpu_topology(n)` (a fixed count) is `{"limit": n, "nosmt": false}` with a note.
  `ignore_user_setting` is dropped.
- `env`: `set_game_drive(x)` is emitted as `PROTON_SET_GAME_DRIVE`. The converter never sees two values for one key that
  have the same `when`: when an env var is set more than once (e.g. around a winetricks call), only the last value is kept.
- Screen placeholders (upstream `util.get_resolution()`), usable in `registry`, `ini` and `env` values: `${screen_w}`
  and `${screen_h}` are the width and height in pixels of the screen the game runs on (the container resolution).
  `${screen_w_h<N>}` (e.g. `${screen_w_h768}`) is the width that keeps that aspect ratio at height `N`:
  `floor(screen_w * N / screen_h)`, from upstream `int(width / height * N)` (steam/497360 sets `Screen Width` to
  it with `Screen Height` 768). `REG_DWORD` values with a screen placeholder are written as the decimal number after
  expansion. Other arithmetic on the resolution stays `unsupported` ("derived screen resolution expression").
- New placeholders: `${user}` (the prefix user name, see above), `${steam_id64}` and `${steam_account_id}`.
  `${steam_id64}` is the signed-in user's 64-bit SteamID (17 digits), which is what upstream `get_steam_account_id()`
  returns and what the SEGA/Square Enix save folders use. `${steam_account_id}` is the 32-bit account id (the
  `userdata/<id>` folder name); only the steam/2246340 override uses it. `${tmp}` and `${random}` are only used
  inside the converter and never reach the output. `${prefix}` is no longer emitted in fix documents.
- Upstream helpers that are no-ops on GameNative: `patch_libcuda()` returns false (no host libcuda), so its dependent
  steps are dropped (umu/umu-starcitizen). `getpass.getuser()` is `${user}`.

## verbs.json

- Top level has an extra `protonfixesCommit`.
- Every verb has `title`, `category` (winetricks category: dlls/fonts/settings/apps) and `source` (`winetricks` or
  `umu-protonfixes`). `source` is `umu-protonfixes` for verbs from `umu-protonfixes/verbs/*.verb`, which upstream
  prefers over winetricks verbs with the same name.
- File entries:
  - `entry` can be a glob (case-insensitive, e.g. `xactengine*.dll`, `Arial*.TTF`). When it is, `install[].name` is `"*"`,
    which means "use the member's own file name, lowercased".
  - `nested` (optional) is a list of members (globs allowed) to open, in order, before `entry`. For example,
    `directx_Jun2010_redist.exe` → `*d3dx9_43*x86*` (a cab) → `d3dx9_43.dll`. When a glob matches several members,
    search all of them.
  - `formats` lists the archive format of each level: `formats[0]` is the download, and each next item is the next `nested` member.
    The values are `cab`, `zip`, `7z`, `tar.gz` and `tar.xz`. A downloaded `.exe` in `cab` format is a self-extracting cabinet, so
    cabextract-style extraction is needed. `formats` is missing (and `entry` is `null`) when the download is the file itself.
  - `install[].name` can contain a subdirectory (e.g. `WindowsPowerShell/v1.0/powershell.exe`).
  - `sha256` is `null` when winetricks has no checksum (e.g. `d3dcompiler_47`).
- Optional fields:
  - `includes`: the verbs that this verb calls (`w_call`). Their files, overrides, registry and fonts are already merged in.
  - `regsvr32`: `[{arch, name}]`. These are DLLs that winetricks registers with `regsvr32` (directplay, the dm* verbs, xact).
    Without this COM registration, the DLLs may not be found. The app can run `wine regsvr32` or skip them.
  - `registry`: on dll/font verbs too (e.g. AppDefaults, font replacements).
  - `version`: the winver that a dll verb also sets.
  - `skipped`: installer runs that the converter skipped. On a `dll` verb this means the result is partial.
    For example, `msxml3` skips `msiexec /i msxml3.msi`, which gives the 32-bit resources DLL.
  - `unparsed`: winetricks lines that the converter could not follow (e.g. `dgvoodoo2`'s `sed` edits of `dgvoodoo.conf`).
  - `note`: e.g. a warning that the source is a Windows service pack, which is a download of several hundred MB.
- `winver` versions are winetricks names (`winxp`, `win7`, `win10`, ...). Winetricks itself passes `winxp64` to winecfg
  on 64-bit prefixes.
- `premark` (vcrun*) has only `overrides`. The extra msvcp140 files that winetricks copies are not listed.
- `allfonts` is a large `font` verb that merges every winetricks font verb (about 42 downloads).

## App-side findings

- `replace_command.from` is now a plain file name or has `regex: true` (see above). Fixed in the converter.
- `replace_command.to` is relative to the directory of the replaced executable (e.g. steam/440900 `../ConanSandbox/...`,
  steam/63110 `./RunDev.exe`), not to `${install}` as the spec says. The app now tries the launcher's directory first, then
  `${install}`. The spec should say this.
- `replace_command` with `to: ""` (argument removal) is skipped by the app. All of these steps are behind
  `{"nosteam": true}`.
- `ini.backup` is ignored: the app applies the INI values on every launch.
- `verbs.json` `regsvr32` lists are ignored: the app does not run regsvr32 (directplay, the dm* verbs, xact, l3codecx).
- `winver` versions other than `win10`, `win7` and `winxp` are skipped. No document uses them at this commit.
