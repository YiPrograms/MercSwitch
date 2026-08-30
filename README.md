# MercSwitch

`mercswitchd` gives compatible MERCURY, TP-Link, and FAST web-managed switches a
Cisco-like SSH CLI and read-only SNMPv2c metrics. It talks to the switch through
the switch's web interface, so no browser is required.

> Do not run `mercswitchctl` against a switch while `mercswitchd` is managing it.

## Quick start

You need Docker with Compose and a host that can reach the switch.

Create a working directory, data directory, and SSH key for the daemon
administrator:

```bash
mkdir -p mercswitch/data mercswitch/deploy/authorized_keys
sudo chown 10001:10001 mercswitch/data
cd mercswitch

if [ ! -f "$HOME/.ssh/mercswitch_admin" ]; then
  ssh-keygen -t ed25519 -f "$HOME/.ssh/mercswitch_admin" -N ""
fi
cp "$HOME/.ssh/mercswitch_admin.pub" deploy/authorized_keys/admin
: > deploy/authorized_keys/viewer
```

Save the following as `compose.yaml`:

```yaml
services:
  mercswitchd:
    image: ${MERCSWITCH_IMAGE:-ghcr.io/yiprograms/mercswitch:latest}
    restart: unless-stopped
    ports:
      - "${DAEMON_BIND_IP:?set DAEMON_BIND_IP}:2222:2222/tcp"
      - "${DAEMON_BIND_IP:?set DAEMON_BIND_IP}:1161:1161/udp"
    environment:
      MERCSWITCH_SWITCH_PASSWORD: ${MERCSWITCH_SWITCH_PASSWORD:?set MERCSWITCH_SWITCH_PASSWORD}
      MERCSWITCH_SNMP_COMMUNITY: ${MERCSWITCH_SNMP_COMMUNITY:?set MERCSWITCH_SNMP_COMMUNITY}
    volumes:
      - ./data:/var/lib/mercswitch
      - ./deploy:/etc/mercswitch:ro
    healthcheck:
      test: ["CMD", "mercswitchd", "healthcheck", "--data-dir", "/var/lib/mercswitch"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    security_opt:
      - no-new-privileges:true
```

Save the following as `deploy/mercswitchd.toml`. Change the switch URL and
username if necessary:

```toml
data_dir = "/var/lib/mercswitch"

[device]
url = "http://192.168.2.251/"
username = "admin"
verify_tls = false

[ssh]
host = "0.0.0.0"
port = 2222
host_key = "/var/lib/mercswitch/ssh_host_ed25519_key"

[ssh.users.admin]
role = "admin"
password_hash = ""
authorized_keys_file = "/etc/mercswitch/authorized_keys/admin"

[ssh.users.viewer]
role = "viewer"
password_hash = ""
authorized_keys_file = "/etc/mercswitch/authorized_keys/viewer"

[snmp]
host = "0.0.0.0"
port = 1161
name = "mercswitch"
contact = ""
location = ""

[poll]
summary_interval = 15.0
detail_interval = 10.0
```

Save `.env` beside `compose.yaml`. It contains the Docker host address, switch
password, and SNMP community, so keep it private. Single-quote values containing
characters such as `$` or `#`:

```dotenv
DAEMON_BIND_IP=192.168.2.10
MERCSWITCH_SWITCH_PASSWORD=change-me
MERCSWITCH_SNMP_COMMUNITY=change-me
MERCSWITCH_IMAGE=ghcr.io/yiprograms/mercswitch:latest
```

```sh
chmod 600 .env
```

Start MercSwitch:

```sh
docker compose pull
docker compose up -d
docker compose ps
```

## Connect

Replace `192.168.2.10` below with `DAEMON_BIND_IP` from `.env`:

```sh
ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@192.168.2.10
```

Useful commands in the Cisco-like shell:

```text
show status
show running-config
show candidate-config
show diff
configure terminal
commit check
commit
write memory
exit
```

Run a one-shot command or replace the managed configuration:

```sh
ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@192.168.2.10 \
  "show running-config"

ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@192.168.2.10 \
  "configure replace stdin" < replacement.cli
```

Query read-only SNMP metrics:

```sh
snmpwalk -v2c -c 'YOUR_SNMP_COMMUNITY' \
  udp:192.168.2.10:1161 1.3.6.1.2.1
```

## Container operations

```sh
docker compose logs -f mercswitchd
docker compose exec mercswitchd mercswitchd healthcheck
docker compose pull && docker compose up -d
docker compose down
```

The `./data` bind mount preserves cached state, backups, journals, and the daemon
SSH host key across container updates. The other bind mount is the read-only
`deploy` configuration directory.

## Standalone direct mode

Use `mercswitchctl` for one-time administration when the daemon is not managing
the switch. It connects directly and prompts for the switch password:

```sh
docker run --rm -it \
  --entrypoint mercswitchctl \
  ghcr.io/yiprograms/mercswitch:latest \
  --url http://192.168.2.251/ probe

docker run --rm -it \
  --entrypoint mercswitchctl \
  ghcr.io/yiprograms/mercswitch:latest \
  --url http://192.168.2.251/ shell
```

## Safety and device support

- Every write downloads a native backup, checks for drift, applies changes in
  dependency order, reads them back, saves to flash, and verifies the result.
- Management address or VLAN changes require explicit confirmation.
- PoE, QoS, mirroring, isolation, and loop protection remain unchanged.
- Published non-Pro SE106, SE106P, SE109, and SE109P units are unmanaged.
  Compatible Pro models must expose the recognized RPM/CGI firmware schema.

The GHCR image supports `linux/amd64` and `linux/arm64`. Both variants are built
on native GitHub-hosted runners without emulation.
