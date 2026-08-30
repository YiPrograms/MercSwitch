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


def test_daemon_secrets_from_environment(tmp_path: Path, monkeypatch):
    config = tmp_path / "daemon.toml"
    config.write_text('[device]\nurl = "http://switch/"\n')
    monkeypatch.setenv("MERCSWITCH_SWITCH_PASSWORD", "switch-secret")
    monkeypatch.setenv("MERCSWITCH_SNMP_COMMUNITY", "snmp-secret")

    settings = DaemonSettings.load(config)

    assert settings.device.password() == "switch-secret"
    assert settings.snmp.community() == "snmp-secret"


def test_daemon_secret_files_remain_supported(tmp_path: Path, monkeypatch):
    password = tmp_path / "password"
    community = tmp_path / "community"
    password.write_text("file-password\n")
    community.write_text("file-community\n")
    config = tmp_path / "daemon.toml"
    config.write_text(
        f'''[device]
url = "http://switch/"
password_file = "{password}"
[snmp]
community_file = "{community}"
'''
    )
    monkeypatch.delenv("MERCSWITCH_SWITCH_PASSWORD", raising=False)
    monkeypatch.delenv("MERCSWITCH_SNMP_COMMUNITY", raising=False)

    settings = DaemonSettings.load(config)

    assert settings.device.password() == "file-password"
    assert settings.snmp.community() == "file-community"
