# mercswitch

`mercswitch` adds a configuration CLI, SSH service, and read-only SNMP agent to compatible
MERCURY, TP-Link, and FAST web-managed switches.

The project contains two independent programs:

- `mercswitchctl` connects directly to a switch for standalone administration.
- `mercswitchd` maintains its own switch session and exposes administration over SSH plus
  metrics over SNMPv2c.

The controller and daemon should not be used against the same switch at the same time. There is
no daemon RPC, Unix socket, or daemon client: administrators SSH directly to `mercswitchd`.

## Development status

The RPM/CGI adapter targets the firmware family verified on the MERCURY SE109 Pro and selects
support using the login algorithm, page variables, endpoints, and form schema—not a model-name
allowlist. Runtime page data determines port count, media, supported speeds, VLAN limits, and
LAG layout. Compatible managed MERCURY, TP-Link, and FAST variants can therefore use the same
adapter. Unrecognized or unmanaged devices fail safely before writes.

Published non-Pro SE106, SE106P, SE109, and SE109P units are unmanaged. Their web-managed Pro
counterparts may be compatible. Each firmware schema must pass a read-only probe and native
backup before separate live write certification.

PoE presence is reported, but PoE settings and metrics are not read or changed in version 1.
QoS, mirroring, isolation, and loop protection are also outside the managed core and are
preserved during `sync`.

## Deployment

Docker Compose is the default deployment. See `deploy/README.md` and the checked-in
`compose.yaml`. A required `DAEMON_BIND_IP` limits TCP 2222 and UDP 1161 to a chosen LAN address;
an optional Linux host-network override is included.

## CLI configuration

Canonical files start with `! mercswitch-config v1` and contain management, VLAN, physical
interface, and port-channel sections. Run `mercswitchctl validate FILE` before a commit or sync.

Profiles contain no password:

```toml
[profiles.lab]
url = "http://192.168.2.251/"
username = "admin"
password_env = "MERCSWITCH_PASSWORD"
verify_tls = false
```

They default to `~/.config/mercswitch/config.toml`. A password may come from the selected
environment variable, an interactive prompt, a file reference, or an optional keyring service.
Pulled state is cached under `~/.cache/mercswitch/PROFILE/`.

Typical standalone use:

```sh
mercswitchctl -p lab probe
mercswitchctl -p lab pull -o running.cli
mercswitchctl -p lab validate candidate.cli
mercswitchctl -p lab diff candidate.cli
mercswitchctl -p lab commit candidate.cli --check
mercswitchctl -p lab commit candidate.cli
mercswitchctl -p lab sync replacement.cli --yes
mercswitchctl -p lab backup
mercswitchctl -p lab restore BACKUP --yes
mercswitchctl -p lab shell
```

`commit` and `sync` pull fresh state and reject drift by default. A management address/VLAN
change additionally requires `--allow-management-change`. Every write operation downloads a
native backup, applies dependency-ordered phases, reads state back, saves to flash, verifies the
managed result, and atomically updates the cache. Failures retain both the journal and backup;
restoration is always explicit.

## Daemon administration

Every SSH session receives an isolated candidate. `viewer` can run show commands; `admin` can
configure, commit, replace from terminal/stdin, and save. A commit takes the daemon-wide writer
lock and performs a fresh drift check. Port forwarding, SFTP, SCP, X11, and agent forwarding are
disabled.

```sh
ssh -p 2222 admin@HOST
ssh -p 2222 viewer@HOST "show status"
ssh -p 2222 admin@HOST "configure replace stdin" < replacement.cli
```

SNMPv2c is read-only. It publishes system identity plus available IF-MIB and RMON Ethernet
packet/error counters. Unsupported counters—including octet counters absent from this firmware—
are omitted instead of fabricated, and all SET requests are rejected.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```
