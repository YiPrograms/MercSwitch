from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .command_engine import CommandSession
from .daemon_config import SshSettings

if TYPE_CHECKING:
    from .daemon import MercSwitchDaemon


class MercSwitchSshServer(asyncssh.SSHServer):
    def __init__(self, settings: SshSettings) -> None:
        self.settings = settings
        self.connection: asyncssh.SSHServerConnection | None = None
        self.password_hasher = PasswordHasher()

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self.connection = conn

    def begin_auth(self, username: str) -> bool:
        user = self.settings.users.get(username)
        if user and user.authorized_keys_file and self.connection:
            path = Path(user.authorized_keys_file)
            if path.exists():
                self.connection.set_authorized_keys(str(path))
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        user = self.settings.users.get(username)
        if not user or not user.password_hash:
            return False
        try:
            return self.password_hasher.verify(user.password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def connection_requested(
        self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
    ) -> bool:
        return False

    def server_requested(self, listen_host: str, listen_port: int) -> bool:
        return False

    def unix_connection_requested(self, dest_path: str) -> bool:
        return False

    def unix_server_requested(self, listen_path: str) -> bool:
        return False


async def handle_ssh_process(process: asyncssh.SSHServerProcess, daemon: MercSwitchDaemon) -> None:
    username = str(process.get_extra_info("username"))
    user = daemon.settings.ssh.users.get(username)
    if user is None or daemon.state is None:
        process.exit(1)
        return
    role = "admin" if user.role == "admin" else "viewer"
    session = CommandSession(daemon.client_proxy, daemon.state, role=role)  # type: ignore[arg-type]
    command = (process.command or "").strip()
    try:
        if command:
            if command in {"configure replace stdin", "configure replace stdin --yes"}:
                session._require_admin()
                session.load_candidate(await process.stdin.read())
                output = await session.execute("commit")
            else:
                output = await session.execute(command)
            if output:
                process.stdout.write(output + ("" if output.endswith("\n") else "\n"))
            process.exit(0)
            return

        process.stdout.write(
            f"mercswitchd {daemon.state.identity.vendor} {daemon.state.identity.model}\n"
        )
        while not session.closed:
            process.stdout.write(session.prompt)
            line = await process.stdin.readline()
            if not line:
                break
            stripped = line.strip()
            if stripped == "configure replace terminal":
                session._require_admin()
                process.stdout.write("paste configuration; finish with end-config\n")
                lines: list[str] = []
                while True:
                    item = await process.stdin.readline()
                    if not item or item.rstrip("\r\n") == "end-config":
                        break
                    lines.append(item)
                session.load_candidate("".join(lines))
                process.stdout.write("candidate loaded\n")
                continue
            try:
                output = await session.execute(stripped)
                if output:
                    process.stdout.write(output + ("" if output.endswith("\n") else "\n"))
            except Exception as exc:
                process.stdout.write(f"% {exc}\n")
        process.exit(0)
    except Exception as exc:
        process.stderr.write(f"% {exc}\n")
        process.exit(1)


async def start_ssh_server(daemon: MercSwitchDaemon) -> asyncssh.SSHAcceptor:
    host_key = Path(daemon.settings.ssh.host_key)
    host_key.parent.mkdir(parents=True, exist_ok=True)
    if not host_key.exists():
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(str(host_key))
        host_key.chmod(0o600)
    return await asyncssh.create_server(
        lambda: MercSwitchSshServer(daemon.settings.ssh),
        daemon.settings.ssh.host,
        daemon.settings.ssh.port,
        server_host_keys=[str(host_key)],
        process_factory=lambda process: handle_ssh_process(process, daemon),
        allow_scp=False,
        sftp_factory=None,
        agent_forwarding=False,
        x11_forwarding=False,
    )
