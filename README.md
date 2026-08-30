# MercSwitch

`mercswitchd` gives compatible MERCURY, TP-Link, and FAST web-managed switches a Cisco-like
SSH CLI and read-only SNMPv2c metrics. The daemon talks to the switch over its web interface;
operators talk directly to the daemon over SSH.

> Do not run the standalone `mercswitchctl` against a switch while `mercswitchd` manages it.

## Quick start

Requirements: Docker with Compose, `ssh-keygen`, Bash, and a host which can reach the switch.

Paste this entire block into a shell. It securely prompts for the switch password and SNMP
community, generates an administrator SSH key if needed, writes the complete Compose setup to
`$HOME/mercswitch`, and starts the published image.

```sh
bash <<'QUICKSTART'
set -euo pipefail

INSTALL_DIR="$HOME/mercswitch"
SSH_KEY="$HOME/.ssh/mercswitch_admin"

read -r -p "Docker host LAN IP: " DAEMON_BIND_IP </dev/tty
read -r -p "Switch URL [http://192.168.2.251/]: " SWITCH_URL </dev/tty
SWITCH_URL="${SWITCH_URL:-http://192.168.2.251/}"
read -r -p "Switch username [admin]: " SWITCH_USERNAME </dev/tty
SWITCH_USERNAME="${SWITCH_USERNAME:-admin}"
read -r -s -p "Switch password: " SWITCH_PASSWORD </dev/tty
printf '\n'
read -r -s -p "SNMP community: " SNMP_COMMUNITY </dev/tty
printf '\n'

if [ -z "$DAEMON_BIND_IP" ] || [ -z "$SWITCH_PASSWORD" ] || [ -z "$SNMP_COMMUNITY" ]; then
  echo "Host IP, switch password, and SNMP community are required." >&2
  exit 1
fi

mkdir -p \
  "$INSTALL_DIR/deploy/authorized_keys" \
  "$INSTALL_DIR/deploy/secrets" \
  "$HOME/.ssh"
chmod 700 "$HOME/.ssh" "$INSTALL_DIR/deploy/secrets"

if [ ! -f "$SSH_KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -f "$SSH_KEY" -C "mercswitch-admin"
fi
cp "$SSH_KEY.pub" "$INSTALL_DIR/deploy/authorized_keys/admin"
: > "$INSTALL_DIR/deploy/authorized_keys/viewer"

umask 077
printf '%s' "$SWITCH_PASSWORD" > "$INSTALL_DIR/deploy/secrets/switch_password"
printf '%s' "$SNMP_COMMUNITY" > "$INSTALL_DIR/deploy/secrets/snmp_community"
unset SWITCH_PASSWORD SNMP_COMMUNITY
umask 022

cat > "$INSTALL_DIR/.env" <<EOF
DAEMON_BIND_IP=$DAEMON_BIND_IP
MERCSWITCH_IMAGE=ghcr.io/yiprograms/mercswitch:latest
EOF

cat > "$INSTALL_DIR/deploy/mercswitchd.toml" <<EOF
data_dir = "/var/lib/mercswitch"

[device]
url = "$SWITCH_URL"
username = "$SWITCH_USERNAME"
password_file = "/run/secrets/switch_password"
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
community_file = "/run/secrets/snmp_community"
name = "mercswitch"
contact = ""
location = ""

[poll]
summary_interval = 15.0
detail_interval = 10.0
EOF

cat > "$INSTALL_DIR/compose.yaml" <<'EOF'
services:
  mercswitchd:
    image: ${MERCSWITCH_IMAGE:-ghcr.io/yiprograms/mercswitch:latest}
    restart: unless-stopped
    ports:
      - "${DAEMON_BIND_IP:?set DAEMON_BIND_IP}:2222:2222/tcp"
      - "${DAEMON_BIND_IP:?set DAEMON_BIND_IP}:1161:1161/udp"
    volumes:
      - mercswitch-data:/var/lib/mercswitch
      - ./deploy/mercswitchd.toml:/etc/mercswitch/mercswitchd.toml:ro
      - ./deploy/authorized_keys:/etc/mercswitch/authorized_keys:ro
    secrets:
      - switch_password
      - snmp_community
    healthcheck:
      test: ["CMD", "mercswitchd", "healthcheck", "--data-dir", "/var/lib/mercswitch"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    security_opt:
      - no-new-privileges:true

volumes:
  mercswitch-data:

secrets:
  switch_password:
    file: ./deploy/secrets/switch_password
  snmp_community:
    file: ./deploy/secrets/snmp_community
EOF

cd "$INSTALL_DIR"
docker compose pull
docker compose up -d
docker compose ps

printf '\nMercSwitch is starting. Connect with:\n'
printf 'ssh -i %s -p 2222 admin@%s\n' "$SSH_KEY" "$DAEMON_BIND_IP"
printf 'Logs: cd %s && docker compose logs -f mercswitchd\n' "$INSTALL_DIR"
QUICKSTART
```

## Use it

Connect to the Cisco-like shell using the command printed by quick start:

```sh
ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@HOST_IP
```

Useful interactive commands:

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
ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@HOST_IP "show running-config"
ssh -i "$HOME/.ssh/mercswitch_admin" -p 2222 admin@HOST_IP \
  "configure replace stdin" < replacement.cli
```

Query read-only SNMP metrics:

```sh
snmpwalk -v2c \
  -c "$(cat "$HOME/mercswitch/deploy/secrets/snmp_community")" \
  udp:HOST_IP:1161 1.3.6.1.2.1
```

Manage the container:

```sh
cd "$HOME/mercswitch"
docker compose logs -f mercswitchd
docker compose exec mercswitchd mercswitchd healthcheck
docker compose pull && docker compose up -d
docker compose down
```

## Standalone direct mode

For one-time administration without the daemon, run the controller directly from the same
GHCR image. It prompts for the switch password:

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

## Safety and support

- Every write downloads a native backup, checks drift, applies ordered phases, reads changes
  back, saves to flash, and verifies the final managed state.
- Management address or VLAN changes require explicit confirmation.
- PoE, QoS, mirroring, isolation, and loop protection remain unchanged.
- Published non-Pro SE106, SE106P, SE109, and SE109P units are unmanaged. Compatible Pro models
  must expose the recognized RPM/CGI firmware schema.

Images are published as `ghcr.io/yiprograms/mercswitch:latest` for `linux/amd64` and
`linux/arm64`. Both architectures are built on native GitHub-hosted runners without emulation.
