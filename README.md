# mercswitch

`mercswitch` adds a Cisco-like configuration CLI, SSH service, and read-only SNMP agent to
compatible MERCURY, TP-Link, and FAST web-managed switches.

The published container image is:

```text
ghcr.io/yiprograms/mercswitch:latest
```

The project contains two independent programs:

- `mercswitchctl` connects directly to a switch for standalone administration.
- `mercswitchd` maintains its own switch session and exposes administration over SSH plus
  metrics over SNMPv2c.

Do not use `mercswitchctl` and `mercswitchd` against the same switch concurrently. There is no
daemon RPC or control client: administrators SSH directly to `mercswitchd`.

## Supported firmware

The RPM/CGI adapter targets the firmware family verified on the MERCURY SE109 Pro. It identifies
compatible firmware from login behavior, page variables, CGI endpoints, and form schemas rather
than a model-name allowlist. Runtime data determines port count, media, speeds, VLAN limits, and
LAG layout.

Published non-Pro SE106, SE106P, SE109, and SE109P units are unmanaged. Their web-managed Pro
counterparts may be compatible. Every new firmware schema should pass a read-only probe and
native backup before separate live-write certification.

PoE presence is reported, but PoE settings and metrics are not managed in version 1. QoS,
mirroring, isolation, and loop protection are also preserved during synchronization.

## Standalone controller with Docker

Create a password-free profile at `~/.config/mercswitch/config.toml`:

```toml
[profiles.lab]
url = "http://192.168.2.251/"
username = "admin"
password_env = "MERCSWITCH_PASSWORD"
verify_tls = false
```

Create the cache directory and define a convenience shell function. It runs the GHCR image as
your local user, mounts configuration read-only, preserves the cache, and makes the current
directory available as `/work`:

```sh
mkdir -p "$HOME/.cache/mercswitch"

mercswitchctl() {
  docker run --rm -it \
    --user "$(id -u):$(id -g)" \
    --env HOME=/home/operator \
    --entrypoint mercswitchctl \
    --volume "$HOME/.config/mercswitch:/home/operator/.config/mercswitch:ro" \
    --volume "$HOME/.cache/mercswitch:/home/operator/.cache/mercswitch" \
    --volume "$PWD:/work" \
    --workdir /work \
    ghcr.io/yiprograms/mercswitch:latest "$@"
}
```

The switch password is requested securely when the profile’s environment variable is not set.
Use the controller normally through the Docker-backed function:

```sh
mercswitchctl -p lab probe
mercswitchctl -p lab pull --output running.cli
mercswitchctl -p lab validate candidate.cli
mercswitchctl -p lab diff candidate.cli
mercswitchctl -p lab commit candidate.cli --check
mercswitchctl -p lab commit candidate.cli
mercswitchctl -p lab sync replacement.cli --check
mercswitchctl -p lab sync replacement.cli --yes
mercswitchctl -p lab write-memory
mercswitchctl -p lab backup
mercswitchctl -p lab restore BACKUP.cfg --yes
mercswitchctl -p lab shell
```

Canonical files begin with `! mercswitch-config v1` and include management, VLAN, physical
interface, and port-channel sections. `commit` and `sync` pull fresh state and reject drift by
default. A management address or VLAN change additionally requires
`--allow-management-change`.

Every write operation first downloads a native backup, applies dependency-ordered phases,
reads each phase back, saves to flash, verifies the complete managed state, and atomically
updates the cache. Failures retain the backup and operation journal; restoration is explicit.

## Daemon deployment with Docker Compose

Download the deployment files from this repository, then copy `.env.example` to `.env`. Set
`DAEMON_BIND_IP` to a specific LAN address on the Docker host:

```dotenv
DAEMON_BIND_IP=192.168.2.10
```

Edit `deploy/mercswitchd.toml`, add public keys to `deploy/authorized_keys/admin` or `viewer`,
and create these untracked secret files:

```text
deploy/secrets/switch_password
deploy/secrets/snmp_community
```

Pull and start the published GHCR image:

```sh
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f mercswitchd
```

Compose uses `ghcr.io/yiprograms/mercswitch:latest` by default. Pin or override it in `.env` when
needed:

```dotenv
MERCSWITCH_IMAGE=ghcr.io/yiprograms/mercswitch:0.1.0
```

Connect directly to the daemon:

```sh
ssh -p 2222 admin@192.168.2.10
ssh -p 2222 viewer@192.168.2.10 "show running-config"
ssh -p 2222 admin@192.168.2.10 "configure replace stdin" < replacement.cli
snmpwalk -v2c -c COMMUNITY udp:192.168.2.10:1161 1.3.6.1.2.1
```

Generate an Argon2id daemon-local password hash with the GHCR image:

```sh
docker run --rm -it \
  ghcr.io/yiprograms/mercswitch:latest hash-password
```

Check the running daemon’s health:

```sh
docker compose exec mercswitchd mercswitchd healthcheck
```

For optional Linux host networking:

```sh
docker compose \
  -f compose.yaml \
  -f compose.host-network.yaml \
  up -d
```

The named volume stores the generated SSH host key, state cache, native backups, journals, and
health state. Configuration and authorized-key mounts are read-only. The container runs as an
unprivileged user with `no-new-privileges`.

## Container publishing

GitHub Actions builds `linux/amd64` natively on `ubuntu-24.04` and `linux/arm64` natively on
`ubuntu-24.04-arm`, then assembles one multi-architecture image. No QEMU emulation is used.
Pull requests build without publishing. Pushes to `main` publish `latest`, `main`, and commit-SHA
tags; tags such as `v0.1.0` also publish semantic-version tags.

```sh
docker pull ghcr.io/yiprograms/mercswitch:latest
```
