# Docker Compose deployment

Docker Compose is the default way to run `mercswitchd`. The container connects outbound to
the switch, listens for SSH on TCP 2222, and serves read-only SNMPv2c on UDP 1161.

1. Copy `.env.example` to `.env` and set `DAEMON_BIND_IP` to a specific LAN address on the
   Docker host. Compose deliberately refuses to start when it is missing.
2. Edit `deploy/mercswitchd.toml`. Add SSH public keys to
   `deploy/authorized_keys/admin` and/or `viewer`.
3. Put only the switch password in `deploy/secrets/switch_password` and the SNMP community in
   `deploy/secrets/snmp_community`. Do not commit these files.
4. Start the daemon:

   ```sh
   docker compose up -d --build
   ```

5. Connect directly to the daemon:

   ```sh
   ssh -p 2222 admin@HOST
   ssh -p 2222 viewer@HOST "show running-config"
   snmpwalk -v2c -c COMMUNITY udp:HOST:1161 1.3.6.1.2.1
   ```

Use `mercswitchd hash-password` to generate an Argon2id hash for a daemon-local password and
place it in the relevant `password_hash` field. Public keys are preferred.

For Linux host networking, use:

```sh
docker compose -f compose.yaml -f compose.host-network.yaml up -d --build
```

The named volume holds the generated SSH host key, current state cache, native backups,
operation journals, and health state. Both configuration and authorized-key mounts are
read-only. The container runs as an unprivileged user with `no-new-privileges`.

Do not run `mercswitchctl` against a switch while `mercswitchd` manages it.

