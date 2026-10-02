# Acquisition (EODMS CLI)

Reproducible, credential-safe tooling for discovering and downloading EODMS
imagery. The only dependency is the **official**
[`eodms-sgdot/eodms-cli`](https://github.com/eodms-sgdot/eodms-cli) installed from
a **pinned source revision** — it is not published on PyPI.

| Item | Value |
| --- | --- |
| Upstream repo | `https://github.com/eodms-sgdot/eodms-cli.git` |
| Pinned revision | `464b94920e7faf28c84a6d31229ef0b2828a1479` |
| Install prefix (git-ignored) | `.tools/eodms-cli` (`src/` = source, `.venv/` = deps) |
| Wrapper | `acquisition/` (Click group: `doctor`, `search`, `download`) |
| Wrapper requirements | `requirements-acquisition.txt` |
| Default collection | `Radarsat-2_Tropical_Forest_Products` |
| Default selection file | `data/interim/selected-scenes.geojson` |
| Default download target | `data/raw` |

There is no service, no scheduler and no pipeline here — only a thin wrapper that
builds upstream argument vectors and invokes the upstream Click CLI **in-process**.

## 1. Install (once, credential-free)

```bash
bash acquisition/bootstrap_eodms_cli.sh
```

The script clones the upstream repo, checks out the pinned revision, creates
`.tools/eodms-cli/.venv`, installs the CLI's own `requirements.txt` plus the two
pinned EODMS API clients (`eodms-py`, `py-eodms-rapi`), and finishes with a
**credential-free help check** (`cli --help`, `cli search --help`,
`cli download --help`) run with `HOME` pointed at a throwaway directory.

Use the bootstrap interpreter for everything else:

```bash
.tools/eodms-cli/.venv/bin/python -m acquisition doctor
```

`doctor` is safe to run at any time: no prompts, no network, no files.

## 2. Exact commands the user runs

All of these are run by the **user**; the agent never authenticates.

```bash
# a) sanity check (no credentials)
.tools/eodms-cli/.venv/bin/python -m acquisition doctor

# b) search the Radarsat-2 tropical forest collection for an AOI + date range
.tools/eodms-cli/.venv/bin/python -m acquisition search \
  --collection Radarsat-2_Tropical_Forest_Products \
  --aoi config/aoi.geojson \
  --datetime 2024-01-01/2024-12-31 \
  --limit 500 \
  --output data/interim/search-results.geojson
#    (add --anonymous to skip the prompts entirely; add --bbox w,s,e,n instead of --aoi)

# c) download the selected scenes  <-- run once data/interim/selected-scenes.geojson exists
.tools/eodms-cli/.venv/bin/python -m acquisition download \
  --collection Radarsat-2_Tropical_Forest_Products \
  --scenes data/interim/selected-scenes.geojson \
  --output-dir data/raw

# d) or download explicit UUIDs
.tools/eodms-cli/.venv/bin/python -m acquisition download \
  --collection Radarsat-2_Tropical_Forest_Products \
  --uuid <uuid-1>,<uuid-2> \
  --output-dir data/raw
```

`search` and `download` prompt for the EODMS username and then (via `getpass`, no
echo) for the password. `--anonymous` on `search` never prompts.

## 3. Credential-handling guarantees (audited)

Verified against the pinned revision by `tests/acquisition/`:

| Risk in upstream | Mitigation in this wrapper |
| --- | --- |
| `eodms_cli configure` writes a **base64-encoded password** to `~/.eodms/config.ini` | `configure` is refused by the wrapper *and* removed from the upstream Click group inside the sandbox (`acquisition/sandbox.py`) |
| `resolve_credentials()` falls back to `~/.eodms/config.ini` | `HOME`/`USERPROFILE` are redirected to a throwaway temp dir for the whole invocation, so the real `~/.eodms` is never read; the temp dir is deleted on exit |
| `-p/--password` on a command line (visible in `ps`, shell history, CI logs) | the wrapper **refuses** `-u/--username/-p/--password`; credentials are injected as **in-memory Click parameter defaults** on the upstream command object |
| `eodms.aaa.AAA_API` persists `~/.eodms/aaa_creds.<user>.<env>.json` (access + refresh tokens) | `AAA_Creds.export_vals`/`import_vals` are neutralised → token state is memory-only; the `HOME` redirect is defence in depth |
| the `cli` group callback calls `_initialize_cli_logging()`, creating `log/eodms_cli.log` inside the CLI source tree | `_initialize_cli_logging` is replaced with a no-op and any `FileHandler` on the `eodms_cli`/`eodms` loggers is removed for the duration of the call |
| credentials persisted in config/env/logs | nothing is written: no config file, no cache, no log file, no `.env` read |

The agent-side test suite uses sentinel credentials
(`sentinel.user@example.invalid` / `s3ntinel-p4ssw0rd-DO-NOT-LOG`) and asserts
that neither the plaintext nor its base64 form appears in `argv`, the isolated
`HOME`, `~/.eodms`, or the upstream `log/` directory. `eodms_cli.make_aaa`,
`make_search` and `make_dds` are replaced with recording doubles in tests, so **no
EODMS authentication or HTTP request is ever performed** by the agent.

## 4. Verified upstream flags

Checked directly from the pinned source (`--help` at revision
`464b949…`), not from memory:

* `search`: `--collection/-c`, `--aoi`, `--bbox/-b`, `--datetime/-d`, `--filter/-f`,
  `--limit/-l`, `--output/-o`, `--env/-e`, `--anonymous`, `--list`, `--queryables`,
  `--uuid2record`, `--orderkey2uuid`, `--input`.
* `download`: `--collection/-c`, `--uuid`, `--input` (CSV/TSV/GeoJSON/JSON/JSONL),
  `--dl_dir`, `--download-dir`, `--limit/-l`, `--env/-e`, `--list`, `--cart-url`.
* `configure` exists upstream and is deliberately **not** exposed.

The wrapper mirrors only the options this project needs and validates them itself
(4-number ordered bbox, ISO-8601 datetime, non-empty output, `--aoi`/`--bbox`
mutual exclusion, non-empty selection file) so errors surface before any network
call.

## 5. Tests

```bash
.tools/eodms-cli/.venv/bin/python -m pytest tests/acquisition -q
```

50 tests: pinned-revision/bootstrap checks, credential-prompt behaviour, argv
leak checks, forbidden-`configure` checks, memory-only AAA state, disabled file
logging, argument validation, and scene-selection mapping. No test performs an
EODMS login or network request.

Tests that need the pinned CLI are **skipped with an explicit message** if
`acquisition/bootstrap_eodms_cli.sh` has not been run.

## 6. Notes for the lead

* `.tools/.gitignore` (contents: `*`) keeps the install out of git; the same
  pattern for `/data/` (raw + interim products) is the only missing line in the
  root `.gitignore`.
* The `aaa_creds` / `config.ini` / `log/` behaviour above is upstream behaviour;
  if the wrapper is ever replaced by a direct upstream call, re-audit it.
