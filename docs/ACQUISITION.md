# Acquisition (EODMS CLI)

Reproducible, credential-safe tooling for discovering and downloading EODMS
imagery. The only dependency is the **official**
[`eodms-sgdot/eodms-cli`](https://github.com/eodms-sgdot/eodms-cli) installed from
a **pinned source revision** — it is not published on PyPI.

| Item | Value |
| --- | --- |
| Upstream repo | `https://github.com/eodms-sgdot/eodms-cli.git` |
| Pinned revision | `464b94920e7faf28c84a6d31229ef0b2828a1479` |
| Install prefix (git-ignored by the root `.gitignore`) | `.tools/eodms-cli` (`src/` = source, `.venv/` = deps) |
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
`cli download --help`) run with the pinned CLI's own home lookups untouched
(no login, no `~/.eodms` read, no network).

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

**No environment variable is modified.** `HOME`, `USERPROFILE` and `CODEX_HOME`
are left exactly as the user's shell set them, and `Path.home()` is never moved.
Each *home-derived lookup* in the pinned sources is redirected in-process, for the
duration of one invocation, to an explicit task-scoped temp dir which is deleted
on exit; every patched attribute is restored afterwards.

Complete audit of home-derived paths in `eodms-cli@464b949…` plus the pinned
`eodms-py` / `py-eodms-rapi` installs:

| Path / behaviour | Source | Mitigation |
| --- | --- | --- |
| `~/.eodms/config.ini` (`USERPROFILE` first) | `eodms_cli.py:122-124` | `_default_config_path` replaced with a sandboxed path |
| `~/.eodms/config.ini`, and the **rename** of `~/.eodms/eodmscli_config.ini` | `config_util.py:25-31` | `config_util.os` shim: `os.path.expanduser` → temp dir (module global only, restored on exit) |
| `<cli src>/config.ini` moved into the config dir | `config_util.py:401-422` | falls inside the shimmed directory |
| `~/.eodms/config.ini` written by `configure` (base64 password) | `eodms_cli.py:398-406` | command removed from the Click group inside the sandbox *and* refused at the wrapper boundary |
| `~/.eodms/aaa_creds.<user>.<env>.json` + token lock | `eodms/aaa.py:127-188` | `eodms.aaa.os` shim + `export_vals`/`import_vals` neutralised → **memory-only** tokens |
| `<cli src>/log/eodms_cli.log` | `eodms_cli.py:169-191, 311` | `_initialize_cli_logging` replaced with a no-op; `FileHandler`s on `eodms_cli`/`eodms` detached for the call |
| `-p/--password` in argv (`ps`, shell history, CI logs) | `eodms_cli.py:2570, 3128` | the wrapper **refuses** `-u/--username/-p/--password`; credentials are injected as **in-memory Click parameter defaults** on the upstream command object, and the group is invoked in-process |
| a drifted/tampered source tree receiving a password | — | every `search`/`download` re-checks the installed revision against the pin **before any prompt** (`require_pinned_revision`) and fails closed; `doctor` is not the only gate |
| injected defaults surviving into a later run | — | the Click parameter defaults are restored in a `finally`, so a subsequent `--anonymous` invocation in the same process cannot inherit a previous password |
| `download --uuid a,b` treated as one bogus UUID | `eodms_cli.py:3129` | upstream `--uuid` is a single, non-multiple option that never splits on commas; the wrapper splits/dedupes and issues **one upstream invocation per UUID** |

Nothing else in the pinned dependencies touches the home directory:
`eodms/config.py` holds service URLs only, `eodms_rapi` writes exclusively to
caller-supplied destinations, and `api_logger` only adds a file handler when a
caller passes `log_file` (nobody does here).

The agent-side test suite uses sentinel credentials
(`sentinel.user@example.invalid` / `s3ntinel-p4ssw0rd-DO-NOT-LOG`) and asserts
that neither the plaintext nor its base64 form appears in `argv`, the isolated
the real `~/.eodms` (created/modified files are compared before and after),
`Path.home()`, or the upstream `log/` directory. Two tests additionally assert
that the **whole environment is byte-identical during and after** an invocation,
that all five audited `~/.eodms` paths resolve inside the task temp dir, and that
every patched attribute is restored. `eodms_cli.make_aaa`,
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

The bootstrap installs the wrapper's pinned dev requirements
(`requirements-acquisition.txt`: `click==8.1.8`, `pytest==8.3.4`) into
`.tools/eodms-cli/.venv`, so the documented command works immediately after a
bootstrap with no extra install step:

```bash
bash acquisition/bootstrap_eodms_cli.sh        # once
.tools/eodms-cli/.venv/bin/python -m pytest tests/acquisition -q
```

72 tests in five files:

| File | What it proves |
| --- | --- |
| `test_boundary_real_aaa.py` | **Boundary evidence.** Runs the *real* `eodms_cli.make_aaa` -> `eodms.aaa.AAA_API` and the *real* `resolve_credentials`/`ConfigUtils` path with every socket operation blocked, so the client is genuinely built and the network boundary is genuinely reached with no traffic. Asserts the real `auth_folder`, `aaa_creds.cred_fn` and token-lock path are all task-scoped; that a decoy `~/.eodms/config.ini` planted in the real home is never read (the prompted credentials are what reach the client) and is byte-identical afterwards; that the real home tree is unchanged; and that no sentinel secret (plaintext or base64) appears in output, exceptions or any file. |
| `test_pin_and_credential_lifecycle.py` | The pin check fails closed **before** any prompt (the prompt functions raise if called) for `search`, `download` and `--anonymous`; and injected Click credential defaults are restored in `finally`, including across the per-UUID download loop, so a later anonymous run starts from `username=None, password=None`. |
| `test_credentials.py` | Prompt behaviour, argv/file/log leak scans with sentinel credentials, forbidden `configure`, memory-only AAA state, disabled file logging, untouched environment and untouched real `~/.eodms`, argument validation, exit-code propagation. |
| `test_bootstrap.py` | Pinned revision, bootstrap invariants, the `.tools` ignore rule, credential-free help check. |
| `test_scenes.py` | Scene-selection parsing and upstream argument mapping, including the single-UUID-per-invocation rule. |

The boundary tests in `test_boundary_real_aaa.py` are the security evidence and
are **not** mocked: they must keep using the real `make_aaa`/`AAA_API` and real
config resolution. `test_credentials.py` does stub the network-facing factories
(`make_aaa`/`make_search`/`make_dds`) purely to drive the command bodies cheaply
— that is a convenience layer, not a substitute for the boundary tests.

Tests that need the pinned CLI are **skipped with an explicit message** if
`acquisition/bootstrap_eodms_cli.sh` has not been run. No test performs an EODMS
login or any network request.

## 6. Notes for the lead

* The install tree `.tools/` is ignored by the root `.gitignore` (lead-owned).
  `/data/` (raw + interim products) should get the same treatment.
  `tests/acquisition/test_bootstrap.py` verifies the ignore rule and **skips
  with an explicit message** if it is not in place yet, so a green run never
  implies the install tree is ignored.
* The `aaa_creds` / `config.ini` / `log/` behaviour above is upstream behaviour;
  if the wrapper is ever replaced by a direct upstream call, re-audit it.
