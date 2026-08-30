# Docker Compose deployment

Docker Compose is the default way to run `mercswitchd`. The container connects outbound to
the switch, listens for SSH on TCP 2222, and serves read-only SNMPv2c on UDP 1161.

1. Copy `.env.example` to `.env`, set the Docker host address, switch password, and SNMP
   community, then run `chmod 600 .env`.
2. Edit `deploy/mercswitchd.toml`. Add SSH public keys to
   `deploy/authorized_keys/admin` and/or `viewer`.
3. Create the bind-mounted data directory for the container's unprivileged UID:

   ```sh
   mkdir -p data
   sudo chown 10001:10001 data
   ```

4. Pull and start the published GHCR image:

   ```sh
   docker compose pull
   docker compose up -d
   ```

5. Connect directly to the daemon:

   ```sh
   ssh -p 2222 admin@HOST
   ssh -p 2222 viewer@HOST "show running-config"
   snmpwalk -v2c -c COMMUNITY udp:HOST:1161 1.3.6.1.2.1
   ```

Generate an Argon2id hash for a daemon-local password with the published image and place it in
the relevant `password_hash` field. Public keys are preferred:

```sh
docker run --rm -it ghcr.io/yiprograms/mercswitch:latest hash-password
```

For Linux host networking, use:

```sh
docker compose -f compose.yaml -f compose.host-network.yaml up -d
```

The `./data` bind mount holds the generated SSH host key, current state cache, native backups,
operation journals, and health state. The whole `deploy` directory is mounted read-only. The
container runs as an unprivileged user with `no-new-privileges`.

Do not run `mercswitchctl` against a switch while `mercswitchd` manages it.
