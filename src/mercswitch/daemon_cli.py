from __future__ import annotations

import asyncio
import json
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from argon2 import PasswordHasher

from .daemon import MercSwitchDaemon
from .daemon_config import DaemonSettings

app = typer.Typer(help="SSH and SNMP daemon for compatible switches.", no_args_is_help=True)


@app.command("hash-password")
def hash_password() -> None:
    password = typer.prompt("SSH password", hide_input=True, confirmation_prompt=True)
    typer.echo(PasswordHasher().hash(password))


@app.command()
def healthcheck(
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("/var/lib/mercswitch"),
    max_age: Annotated[int, typer.Option("--max-age")] = 60,
) -> None:
    try:
        payload = json.loads((data_dir / "health.json").read_text())
        updated = datetime.fromisoformat(payload["updated_at"])
        age = (datetime.now(UTC) - updated).total_seconds()
        if payload.get("status") != "ok" or age > max_age:
            raise ValueError(f"stale or unhealthy daemon state ({age:.0f}s)")
    except Exception as exc:
        typer.echo(f"unhealthy: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("ok")


@app.command()
def run(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path(
        "/etc/mercswitch/mercswitchd.toml"
    ),
) -> None:
    async def main() -> None:
        daemon = MercSwitchDaemon(DaemonSettings.load(config))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, daemon.stop_event.set)
        try:
            await daemon.run()
        finally:
            await daemon.close()

    asyncio.run(main())


if __name__ == "__main__":
    app()
