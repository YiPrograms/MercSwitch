from argon2 import PasswordHasher

from mercswitch.daemon_config import SshSettings, SshUserSettings
from mercswitch.ssh_server import MercSwitchSshServer


def test_password_auth_and_forwarding_rejection():
    settings = SshSettings(
        users={
            "viewer": SshUserSettings(
                role="viewer", password_hash=PasswordHasher().hash("viewer-pass")
            )
        }
    )
    server = MercSwitchSshServer(settings)
    assert server.password_auth_supported()
    assert server.validate_password("viewer", "viewer-pass")
    assert not server.validate_password("viewer", "incorrect")
    assert not server.validate_password("missing", "viewer-pass")
    assert server.connection_requested("example.com", 80, "127.0.0.1", 1000) is False
    assert server.server_requested("0.0.0.0", 8080) is False
    assert server.unix_connection_requested("/tmp/socket") is False
    assert server.unix_server_requested("/tmp/socket") is False
