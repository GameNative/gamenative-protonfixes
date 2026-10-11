# protonfixes converter

`convert.py` converts [umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes) game fixes and
the [winetricks](https://github.com/Winetricks/winetricks) verbs they use into the JSON data in `data/`. The app downloads this data at launch. The format is in the shared spec. `SCHEMA_NOTES.md` lists every
place where the data differs from the spec or adds to it.

The converter never imports or runs upstream code. It parses fix files with `ast`, and interprets them
symbolically. It reads the winetricks shell script as text. Treat both inputs as untrusted data.

## Run it

Requires Python 3.11+ (standard library only).

```sh
git clone https://github.com/Open-Wine-Components/umu-protonfixes /tmp/pf
git -C /tmp/pf submodule update --init --depth 1 subprojects/umu-database
git clone --depth 1 https://github.com/Winetricks/winetricks /tmp/winetricks

python3 -I -m unittest discover tools/protonfixes/tests
python3 -I tools/protonfixes/convert.py \
  --protonfixes /tmp/pf \
  --umu-database /tmp/pf/subprojects/umu-database/umu-database.csv \
  --winetricks /tmp/winetricks/src/winetricks \
  --out data
```

Options:

- `--umu-database`: defaults to `<protonfixes>/umu-database.csv`.
- `--upstream-commit`, `--upstream-date`, `--winetricks-commit`: these are taken from git when the inputs are checkouts.
- `--no-per-file-commits`: use the upstream HEAD for every document's `upstreamCommit`.
  The default is the last commit that touched each fix. For this, clone without `--depth`; `--filter=blob:none` is enough.

The output is deterministic. `index.json` keeps its old `generatedAt` when nothing else changed, so a run on
unchanged inputs gives an empty diff.

## Output

- `data/<store>/<id>.json`: one document per store id. Symlinked upstream fixes and extra store codenames
  each get their own document, with `canonical` pointing to the real file's document.
- `data/<store>/default.json`: the store-wide `default.py` (steps for every game of the store, and the
  `-pf_*` launch options it understands). `SCHEMA_NOTES.md` describes the format.
- `data/index.json`: one entry per document, with its sha256 and coverage, and a `defaults` map for the
  store defaults.
- `data/verbs.json`: the winetricks verbs that the fixes use, plus common verbs from `DEFAULT_EXTRA_VERBS`.
- `data/REPORT.md`: counts (with and without overrides), unsupported reasons, partial and `none` documents,
  the state of every override, sequencing hazards, and verbs by kind.

Only the store directories (`steam`, `gog`, ...) are cleaned of stale files. Other directories under `data/`
(e.g. `valve/`) are left alone.

Coverage: `full` means every step was converted, `partial` means some steps are `unsupported`, and `none` means nothing
usable was converted. A `verb` step counts as converted even when that verb is `unsupported` in `verbs.json`.
`REPORT.md` also gives the number of documents that are full and use only supported verbs.

## How the data is updated

`.github/workflows/protonfixes-sync.yml` runs every Monday, and on manual dispatch. The top-level `README.md`
describes its steps. It commits `data/` to the `published` branch and, when the R2 secrets are set, uploads it to
`protonfixes/<version>/` and updates `protonfixes/latest.json`. Review `data/REPORT.md` after each run.

## Extending the converter

When a new upstream fix uses a pattern that the converter does not know, it emits an `unsupported` step with the
exact source snippet. `REPORT.md` shows these. To support a pattern, add a case in `FixInterp` (util helpers are in
`util_call`, and other calls are in `call_qual` / `call_method`). Then add a test in `tests/test_convert.py`.

When a fix is too imperative to convert (threads, regex rewrites, loops over host state), port it by hand in
`overrides/<store>/<stem>.json` (format in `SCHEMA_NOTES.md`). Set `upstreamSha256` to the sha256 of the upstream
file:

```sh
python3 -I -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' /tmp/pf/gamefixes-steam/3590.py
```

When upstream changes that file, `REPORT.md` marks the override `stale` and the converter output is used until the
override is reviewed and its hash updated.
