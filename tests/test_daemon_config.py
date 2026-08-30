from pathlib import Path

from mercswitch.daemon_config import DaemonSettings


def test_daemon_toml_roles_and_ports(tmp_path: Path):
    config = tmp_path / "daemon.toml"
    config.write_text(
        """
data_dir = "/tmp/mercswitch-test"
[device]
url = "http://switch/"
password_file = "/run/secrets/switch_password"
[ssh]
port = 2222
[ssh.users.admin]
role = "admin"
password_hash = "$argon2id$test"
[snmp]
port = 1161
"""
    )
    settings = DaemonSettings.load(config)
    assert settings.ssh.users["admin"].role == "admin"
    assert settings.ssh.port == 2222
    assert settings.snmp.port == 1161
