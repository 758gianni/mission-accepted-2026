"""User-run wrapper around the pinned official EODMS CLI.

Run it with the bootstrap virtualenv interpreter:

    .tools/eodms-cli/.venv/bin/python -m acquisition doctor

The wrapper builds an upstream ``search``/``download`` argument vector and calls
the upstream Click group **in-process** (never a subprocess, never
``-p``/``--password`` on a command line). Credentials are prompted for only when
the chosen command actually needs them and are injected as in-memory Click
parameter defaults, inside a sandbox that has no access to the user's
``~/.eodms``, no CLI file logging and no on-disk AAA tokens.
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Sequence

import click

from . import (
    DEFAULT_COLLECTION,
    DEFAULT_RAW_DIR,
    DEFAULT_SCENES_FILE,
    EODMS_CLI_REPO,
    EODMS_CLI_REV,
    FORBIDDEN_UPSTREAM_COMMANDS,
    UPSTREAM_SRC_DIR,
    VENV_DIR,
    __version__,
)
from . import scenes as scenes_mod
from .credentials import (
    CredentialError,
    obtain_credentials,
    prompt_password,
    prompt_username,
)
from .sandbox import isolated_eodms_environment

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_SRC_PATH = REPO_ROOT / UPSTREAM_SRC_DIR
UPSTREAM_SRC_DIR = str(UPSTREAM_SRC_PATH)
VENV_PYTHON_PATH = REPO_ROOT / VENV_DIR / "bin" / "python"

#: Options that would put a secret in argv / shell history. Refused outright.
_CREDENTIAL_OPTIONS = {"-u", "--username", "-p", "--password"}

_PENDING_ARGS_KEY = "acquisition.pending_args"

_FORBIDDEN_HINT = (
    "Refusing {option!r} on the command line: credentials must be entered "
    "interactively and are never passed in argv, env, config or logs. Use "
    "--anonymous for unauthenticated access."
)


def load_upstream() -> Any:
    """Import the pinned upstream ``eodms_cli`` module from .tools/eodms-cli/src."""
    if not (UPSTREAM_SRC_PATH / "eodms_cli.py").is_file():
        raise click.ClickException(
            f"Pinned EODMS CLI not found at {UPSTREAM_SRC_PATH}. "
            "Run: bash acquisition/bootstrap_eodms_cli.sh"
        )
    if str(UPSTREAM_SRC_PATH) not in sys.path:
        sys.path.insert(0, str(UPSTREAM_SRC_PATH))
    import eodms_cli  # noqa: PLC0415

    return eodms_cli


def _upstream_revision() -> str:
    git_dir = UPSTREAM_SRC_PATH / ".git"
    if not git_dir.exists():
        return "unknown (source tree has no .git)"
    try:
        return subprocess.run(
            ["git", "-C", str(UPSTREAM_SRC_PATH), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown (git unavailable)"


def _reject_credential_options(args: Sequence[str]) -> None:
    for arg in args:
        option = arg.split("=", 1)[0]
        if option in _CREDENTIAL_OPTIONS:
            raise CredentialError(_FORBIDDEN_HINT.format(option=option))


def require_pinned_revision() -> str:
    """Fail closed unless the installed CLI source is exactly the pinned revision.

    Runs on every ``search``/``download`` **before** any credential prompt, so a
    drifted or tampered source tree can never receive a password.
    """
    revision = _upstream_revision()
    if revision != EODMS_CLI_REV:
        raise CredentialError(
            f"Installed EODMS CLI revision {revision!r} != pinned {EODMS_CLI_REV}. "
            "Refusing to prompt for credentials or contact EODMS with an unverified "
            "CLI. Re-run: bash acquisition/bootstrap_eodms_cli.sh"
        )
    return revision


def _apply_credential_defaults(command: Any, username: str | None, password: str | None):
    """Inject credentials as in-memory Click defaults (never as argv).

    Returns a restore callable: the defaults are removed again in ``finally`` so a
    later anonymous invocation in the same process cannot inherit them.
    """
    if not username and not password:
        return lambda: None

    saved: list[tuple[Any, Any]] = []
    for param in command.params:
        if param.name == "username" and username:
            saved.append((param, param.default))
            param.default = username
        elif param.name == "password" and password:
            saved.append((param, param.default))
            param.default = password

    def restore() -> None:
        for param, previous in reversed(saved):
            param.default = previous

    return restore


def _run_upstream(
    argv: Sequence[str] | Sequence[Sequence[str]],
    *,
    anonymous: bool = False,
    needs_credentials: bool = True,
    username_prompt=None,
    password_prompt=None,
) -> None:
    argvs: list[list[str]] = (
        [list(argv)] if argv and isinstance(argv[0], str) else [list(a) for a in argv]  # type: ignore[index]
    )
    # Pin check first: a drifted source tree must never be prompted for, let alone
    # sent credentials.
    require_pinned_revision()
    upstream = load_upstream()
    username, password = obtain_credentials(
        anonymous=anonymous,
        needs_credentials=needs_credentials,
        username_prompt=username_prompt,
        password_prompt=password_prompt,
    )
    with isolated_eodms_environment(upstream):
        for single_argv in argvs:
            command = upstream.cli.commands.get(single_argv[0])
            if command is None:
                raise click.ClickException(
                    f"Pinned EODMS CLI has no {single_argv[0]!r} command (found: "
                    f"{', '.join(sorted(upstream.cli.commands))})."
                )
            restore_defaults = _apply_credential_defaults(command, username, password)
            try:
                upstream.cli.main(
                    args=single_argv,
                    prog_name="eodms-cli (pinned, invoked by acquisition)",
                    standalone_mode=True,
                )
            finally:
                restore_defaults()


# --------------------------------------------------------------------------
# wrapper CLI
# --------------------------------------------------------------------------


class _GuardedGroup(click.Group):
    """Group that vets the *remaining* tokens before the subcommand runs."""

    def parse_args(self, ctx, args):
        rest = super().parse_args(ctx, args)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            protected = list(getattr(ctx, "protected_args", None) or [])
        pending = protected + list(ctx.args or [])
        ctx.meta[_PENDING_ARGS_KEY] = pending
        # Vet before Click resolves the subcommand: Click 8.2+ resolves the
        # command before invoking the group callback, so a late check would
        # never see a forbidden command name.
        for name in FORBIDDEN_UPSTREAM_COMMANDS:
            if name in pending:
                raise CredentialError(
                    f"Upstream command {name!r} is forbidden by this wrapper: it "
                    "writes a base64-encoded password to ~/.eodms/config.ini. "
                    "Credentials are prompted for instead."
                )
        _reject_credential_options(pending)
        return rest


@click.group(
    cls=_GuardedGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=False,
)
@click.version_option(__version__, "-V", "--version", prog_name="acquisition")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Credential-safe wrapper around the pinned official EODMS CLI.

    Commands: doctor (credential-free), search, download.
    """
    obj = ctx.ensure_object(dict)
    obj.setdefault("username_prompt", prompt_username)
    obj.setdefault("password_prompt", prompt_password)


@main.command("doctor")
def doctor() -> None:
    """Verify the pinned CLI install. No credentials, no prompts, no network."""
    revision = _upstream_revision()
    click.echo(f"acquisition wrapper {__version__}")
    click.echo(f"eodms-cli repo : {EODMS_CLI_REPO}")
    click.echo(f"pinned revision: {EODMS_CLI_REV}")
    click.echo(f"installed revision: {revision}")
    click.echo(f"source tree    : {UPSTREAM_SRC_PATH}")
    click.echo(f"virtualenv     : {VENV_PYTHON_PATH}")
    if not (UPSTREAM_SRC_PATH / "eodms_cli.py").is_file():
        raise click.ClickException(
            "Pinned EODMS CLI is not installed. Run: "
            "bash acquisition/bootstrap_eodms_cli.sh"
        )
    ok = revision == EODMS_CLI_REV
    click.echo(f"revision match : {'yes' if ok else 'NO'}")
    if not ok:
        raise click.ClickException(
            f"Installed EODMS CLI revision {revision} != pinned {EODMS_CLI_REV}. "
            "Re-run: bash acquisition/bootstrap_eodms_cli.sh"
        )
    upstream = load_upstream()
    with isolated_eodms_environment(upstream) as sandbox:
        removed = ", ".join(sandbox.removed_commands) or "none"
        click.echo(f"forbidden upstream commands removed while running: {removed}")
        click.echo("file logging : disabled (no log file is written)")
        click.echo("config dir   : task-scoped temp dir (environment and Path.home() untouched)")
        click.echo(f"sandbox root : {sandbox.root} (removed on exit)")
        click.echo("AAA tokens   : memory-only (never written to disk)")
    click.echo(
        "upstream commands present: "
        + ", ".join(sorted(upstream.cli.commands))
        + "  (configure is never invoked by this wrapper)"
    )
    click.echo("no credentials are required or read by 'doctor'.")


@main.command("search")
@click.option("--collection", "-c", default=DEFAULT_COLLECTION, show_default=True,
              help="STAC collection, e.g. Radarsat-2_Tropical_Forest_Products.")
@click.option("--aoi", type=click.Path(exists=True, dir_okay=False), default=None,
              help="AOI file (1-5 polygons) to intersect with.")
@click.option("--bbox", "-b", default=None, help=scenes_mod.BBOX_HELP)
@click.option("--datetime", "-d", "datetime_range", default=None,
              help=scenes_mod.DATETIME_HELP)
@click.option("--filter", "-f", "filter_text", default=None,
              help="CQL2 text filter passed through to the upstream CLI.")
@click.option("--limit", "-l", type=int, default=None,
              help="Max items (upstream default: 1000).")
@click.option("--output", "-o", default=None,
              help="Write results to this GeoJSON file.")
@click.option("--env", "-e", "environment", default="prod", show_default=True)
@click.option("--anonymous", is_flag=True,
              help="Search anonymously (never prompts for credentials).")
@click.pass_context
def search_cmd(ctx: click.Context, collection: str, aoi: str | None, bbox: str | None,
               datetime_range: str | None, filter_text: str | None, limit: int | None,
               output: str | None, environment: str, anonymous: bool) -> None:
    """Search the catalogue by AOI/date range and export GeoJSON."""
    if not (collection or "").strip():
        raise CredentialError("--collection is required.")
    if aoi and bbox:
        raise CredentialError("Use either --aoi or --bbox, not both.")
    if not aoi and not bbox:
        raise CredentialError("Provide an AOI with --aoi or a --bbox west,south,east,north.")
    if bbox:
        try:
            bbox = scenes_mod.validate_bbox(bbox)
        except ValueError as exc:
            raise CredentialError(f"Invalid --bbox: {exc}") from exc
    if datetime_range:
        try:
            datetime_range = scenes_mod.validate_datetime(datetime_range)
        except ValueError as exc:
            raise CredentialError(f"Invalid --datetime: {exc}") from exc
    if output is not None and not str(output).strip():
        raise CredentialError("--output must not be empty.")

    argv = scenes_mod.search_argv(
        collection=collection.strip(),
        aoi=aoi,
        bbox=bbox,
        datetime_range=datetime_range,
        limit=limit,
        output=output,
        filter_text=filter_text,
        env=environment,
        anonymous=anonymous,
    )
    _run_upstream(
        argv,
        anonymous=anonymous,
        needs_credentials=not anonymous,
        username_prompt=lambda: ctx.obj["username_prompt"](),
        password_prompt=lambda: ctx.obj["password_prompt"](),
    )


@main.command("download")
@click.option("--collection", "-c", default=DEFAULT_COLLECTION, show_default=True,
              help="Collection the selected scenes belong to.")
@click.option("--scenes", type=click.Path(exists=True, dir_okay=False), default=None,
              help=f"Selection GeoJSON/JSON/JSONL (default: {DEFAULT_SCENES_FILE}).")
@click.option("--uuid", "uuids", multiple=True,
              help="Item UUID(s); repeat or comma-separate. Mutually exclusive with --scenes.")
@click.option("--output-dir", default=DEFAULT_RAW_DIR, show_default=True,
              help="Destination directory for raw scenes.")
@click.option("--limit", "-l", type=int, default=100, show_default=True)
@click.option("--env", "-e", "environment", default="prod", show_default=True)
@click.pass_context
def download_cmd(ctx: click.Context, collection: str, scenes: str | None,
                 uuids: tuple[str, ...], output_dir: str, limit: int,
                 environment: str) -> None:
    """Download the selected scenes into the raw data directory."""
    if not (collection or "").strip():
        raise CredentialError("--collection is required.")

    uuid_list = scenes_mod.normalise_uuids(uuids)
    if scenes and uuid_list:
        raise CredentialError("Use either --scenes or --uuid, not both.")

    scenes_path: str | None = None
    if not uuid_list:
        scenes_path = scenes or DEFAULT_SCENES_FILE
        if not os.path.exists(scenes_path):
            raise CredentialError(
                f"No selection found at {scenes_path}. Create it first, or pass "
                "--uuid <item-uuid>."
            )
        try:
            scenes_mod.load_scenes(scenes_path)
        except ValueError as exc:
            raise CredentialError(str(exc)) from exc
    if not str(output_dir).strip():
        raise CredentialError("--output-dir must not be empty.")

    argvs = scenes_mod.download_argvs(
        collection=collection.strip(),
        scenes_path=scenes_path,
        uuids=uuid_list,
        output_dir=output_dir,
        env=environment,
        limit=limit,
    )
    _run_upstream(
        argvs,
        anonymous=False,
        needs_credentials=True,
        username_prompt=lambda: ctx.obj["username_prompt"](),
        password_prompt=lambda: ctx.obj["password_prompt"](),
    )


def run() -> None:
    """Entry point used by ``python -m acquisition``."""
    try:
        main(standalone_mode=True)
    except SystemExit as exc:  # pragma: no cover - propagated for the shell
        raise exc


if __name__ == "__main__":  # pragma: no cover
    run()
