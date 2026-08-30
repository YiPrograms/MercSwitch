from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .client import MercSwitchClient
from .command_engine import CommandSession
from .config import config_diff, parse_config, render_config, validate_candidate
from .errors import MercSwitchError
from .profiles import DeviceProfile, default_profile_path, load_profile
from .storage import CacheStore, default_cache_root

app = typer.Typer(
    help="Direct controller for compatible web-managed switches.", no_args_is_help=True
)
console = Console()


@dataclass(slots=True)
class Context:
    profile: DeviceProfile
    cache: CacheStore

    def client(self) -> MercSwitchClient:
        return MercSwitchClient(
            self.profile.url,
            self.profile.username,
            self.profile.password(),
            cache_dir=self.cache.root,
            verify_tls=self.profile.verify_tls,
        )


@app.callback()
def main(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p")] = "default",
    config: Annotated[Path, typer.Option("--config")] = default_profile_path(),
    url: Annotated[str | None, typer.Option("--url")] = None,
    username: Annotated[str, typer.Option("--username")] = "admin",
    password_env: Annotated[str, typer.Option("--password-env")] = "MERCSWITCH_PASSWORD",
) -> None:
    try:
        selected = (
            DeviceProfile(profile, url, username, password_env=password_env)
            if url
            else load_profile(profile, config)
        )
        ctx.obj = Context(selected, CacheStore(default_cache_root() / profile))
    except MercSwitchError as exc:
        raise typer.BadParameter(str(exc)) from exc


async def _with_client(ctx: Context, action):
    async with ctx.client() as client:
        return await action(client)


def _fail(exc: Exception) -> None:
    console.print(f"[red]error:[/red] {exc}", highlight=False)
    raise typer.Exit(1)


@app.command()
def probe(ctx: typer.Context, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    async def run(client: MercSwitchClient):
        identity = await client.probe()
        capabilities = await client.adapter.read_capabilities()
        return identity, capabilities

    try:
        identity, capabilities = asyncio.run(_with_client(ctx.obj, run))
        payload = asdict(identity) | {"capabilities": asdict(capabilities)}
        console.print_json(json.dumps(payload)) if json_output else console.print(payload)
    except Exception as exc:
        _fail(exc)


@app.command()
def pull(
    ctx: typer.Context,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    try:
        state = asyncio.run(_with_client(ctx.obj, lambda client: client.pull()))
        text = render_config(state)
        if output:
            output.write_text(text)
            console.print(f"wrote {output}")
        else:
            console.print(text, highlight=False, end="")
    except Exception as exc:
        _fail(exc)


@app.command()
def show(ctx: typer.Context, live: Annotated[bool, typer.Option("--live")] = False) -> None:
    try:
        state = (
            asyncio.run(_with_client(ctx.obj, lambda client: client.pull()))
            if live
            else ctx.obj.cache.load_state()
        )
        console.print(render_config(state), highlight=False, end="")
    except Exception as exc:
        _fail(exc)


@app.command("diff")
def diff_command(ctx: typer.Context, file: Path) -> None:
    try:
        state = ctx.obj.cache.load_state()
        candidate = parse_config(file.read_text(), base_hash=state.managed_hash())
        console.print(config_diff(state, candidate) or "no changes\n", highlight=False, end="")
    except Exception as exc:
        _fail(exc)


@app.command("validate")
def validate_command(ctx: typer.Context, file: Path) -> None:
    try:
        state = ctx.obj.cache.load_state()
        candidate = parse_config(file.read_text(), base_hash=state.managed_hash())
        validate_candidate(candidate, state.capabilities)
        console.print("configuration is valid")
    except Exception as exc:
        _fail(exc)


def _apply_file(
    ctx: Context,
    file: Path,
    *,
    check: bool,
    force: bool,
    allow_management_change: bool,
) -> None:
    async def run(client: MercSwitchClient):
        base = ctx.cache.load_state()
        candidate = parse_config(file.read_text(), base_hash=base.managed_hash())
        return await client.commit(
            candidate,
            check=check,
            force=force,
            allow_management_change=allow_management_change,
            progress=lambda message: console.print(f"[dim]{message}[/dim]"),
        )

    result = asyncio.run(_with_client(ctx, run))
    console.print(result.message)
    if result.plan:
        for change in result.plan.changes:
            console.print(f"{change.phase:02d} {change.action} {change.target}")


@app.command()
def commit(
    ctx: typer.Context,
    file: Annotated[Path | None, typer.Argument()] = None,
    check: Annotated[bool, typer.Option("--check")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
    allow_management_change: Annotated[bool, typer.Option("--allow-management-change")] = False,
) -> None:
    try:
        candidate_path = file or (ctx.obj.cache.root / "candidate.cli")
        _apply_file(
            ctx.obj,
            candidate_path,
            check=check,
            force=force,
            allow_management_change=allow_management_change,
        )
    except Exception as exc:
        _fail(exc)


@app.command()
def sync(
    ctx: typer.Context,
    file: Path,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
    allow_management_change: Annotated[bool, typer.Option("--allow-management-change")] = False,
) -> None:
    if (
        not check
        and not yes
        and not typer.confirm("Replace the complete managed core configuration?")
    ):
        raise typer.Abort()
    try:
        _apply_file(
            ctx.obj,
            file,
            check=check,
            force=force,
            allow_management_change=allow_management_change,
        )
    except Exception as exc:
        _fail(exc)


@app.command("write-memory")
def write_memory(ctx: typer.Context) -> None:
    try:
        asyncio.run(_with_client(ctx.obj, lambda client: client.write_memory()))
        console.print("configuration saved")
    except Exception as exc:
        _fail(exc)


@app.command()
def backup(
    ctx: typer.Context, output: Annotated[Path | None, typer.Option("--output", "-o")] = None
) -> None:
    try:
        path = asyncio.run(_with_client(ctx.obj, lambda client: client.backup(output)))
        console.print(path)
    except Exception as exc:
        _fail(exc)


@app.command()
def restore(
    ctx: typer.Context, file: Path, yes: Annotated[bool, typer.Option("--yes")] = False
) -> None:
    if not yes and not typer.confirm(
        "Restore this native backup and replace the switch configuration?"
    ):
        raise typer.Abort()
    try:
        result = asyncio.run(_with_client(ctx.obj, lambda client: client.restore(file)))
        console.print(result.message)
    except Exception as exc:
        _fail(exc)


@app.command()
def shell(ctx: typer.Context) -> None:
    async def run(client: MercSwitchClient) -> None:
        session = CommandSession(client, await client.pull(), allow_local_files=True)
        while not session.closed:
            try:
                line = await asyncio.to_thread(input, session.prompt)
                output = await session.execute(line)
                if output:
                    console.print(
                        output, highlight=False, end="" if output.endswith("\n") else "\n"
                    )
                client.store.save_candidate(render_config(session.candidate))
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as exc:
                console.print(f"% {exc}")

    try:
        asyncio.run(_with_client(ctx.obj, run))
    except Exception as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
