# Deploying WideMind for the pilot

A single Linux host with systemd, running every service on loopback behind Caddy for HTTPS. The steps end when `npm run pilot:preflight` passes. Unit files are in [`deployment/`](../deployment) (web, worker, business, tools, alerts, snapshot and backup timers, Caddy) and [`deploy/`](../deploy) (economy and its reconciliation worker).

## Layout

| Path | Owner | Contents |
| --- | --- | --- |
| `/opt/widemind` | root (read-only to services) | Checkout, `dist/`, `.venvs/business`, `.venvs/tools` |
| `/opt/widemind/.env` | widemind, 0600 | Settings and secrets ([example](../deployment/widemind.env.example)) |
| `/var/lib/widemind` | widemind, 0700 | Runtime, business and tools databases; tools workspace |
| `/opt/widemind-economy`, `/var/lib/widemind-economy` | widemind-economy | Economy service and ledger, outside the agent's write access |
| `/etc/widemind/economy.env` | root, 0600 | Economy settings and PayPal credentials |
| `/var/backups/widemind` | root:widemind, 0750 | Backups; copies inside are root-only, `latest.json` is readable for monitoring |

## Steps

1. **Host and users.** Install Node 24, Python 3.12+, SQLite and Caddy. Create the `widemind` and `widemind-economy` system users. Point the domain's DNS at the host.
2. **Code.** Clone into `/opt/widemind`, then:
   ```sh
   npm ci && npm run build && npm run services:setup
   ```
   Install the economy package into `/opt/widemind-economy/.venv` as described in `deploy/widemind-economy.service` and [ECONOMY_INTEGRATION.md](ECONOMY_INTEGRATION.md).
3. **Settings.** Copy `deployment/widemind.env.example` to `/opt/widemind/.env` and fill it in. Generate each service token separately (`openssl rand -hex 32`) and the operator hash with `npm run operator:hash`. Give the economy the matching tokens in `/etc/widemind/economy.env`. Keep `WIDEMIND_ENABLE_LIVE_EFFECTS=false` and sandbox modes for now.
4. **Data.** `npm run migrate` as the `widemind` user. Sign in at `/operator`; on first sign-in it opens Getting started, where you create the agent ([GETTING_STARTED.md](GETTING_STARTED.md)). Create `/var/backups/widemind` as `root:widemind` mode 0750.
5. **Services.** Copy the unit files to `/etc/systemd/system/`, then:
   ```sh
   systemctl daemon-reload
   systemctl enable --now widemind-economy widemind-economy-worker widemind-tools widemind-business widemind-web widemind-worker widemind-alerts
   systemctl enable --now widemind-backup.timer widemind-snapshot.timer
   ```
   `npm run services:status` checks the loopback services and `npm run status` the worker.
6. **HTTPS.** Put `deployment/Caddyfile` (with your domain) in `/etc/caddy/Caddyfile` and reload Caddy. Only the web app is exposed.
7. **Accounts.** Register the PayPal webhook at `https://<domain>/api/modules/economy/webhook` and complete Gmail OAuth ([GMAIL.md](GMAIL.md)) and the OpenAI key ([AI-PROVIDERS.md](AI-PROVIDERS.md)). To start without a mailbox, set `WIDEMIND_PORTAL_ONLY=true`: customers then use their private link only and the email check passes.
8. **Sandbox rehearsal.** Open the run in sandbox (`npm run pilot:init -- --sandbox`), then:
   ```sh
   npm run pilot:rehearse -- --email <an inbox you control>
   ```
   It places one order, prints the PayPal sandbox checkout link for you to pay with a sandbox buyer account, waits for delivery, and then checks that the report, an unpublished snapshot, the public site, alerts, a test webhook notification and backup freshness agree. Every check must pass. It refuses to run with live commerce, live effects or a live payment provider.
9. **Go live.** Switch to live credentials and modes, set `WIDEMIND_ENABLE_LIVE_EFFECTS=true`, open the run with verified live $5 funding through the economy operator flow, and restart the services. Then:
   ```sh
   npm run backup && npm run backup:verify
   npm run pilot:preflight
   ```
   Every check must pass before taking real customer work.

## Backups and restore

The backup timer runs every six hours. Each run copies every configured database with SQLite's online backup API, checks each copy with `PRAGMA integrity_check`, records SHA-256 checksums in a manifest, and only then marks the backup complete. The `ExecStartPost` step re-verifies it, including every run's snapshot hash chain. `WIDEMIND_BACKUP_KEEP` complete backups are kept. The `backup_stale` alert and the `backups_recent` preflight check fire when the newest backup is older than `WIDEMIND_BACKUP_MAX_AGE_HOURS`.

Backed up: the runtime database, `WIDEMIND_ECONOMY_DB`, `WIDEMIND_BUSINESS_DB`, every SQLite file under `WIDEMIND_TOOLS_STATE`, and any `label=/path` entries in `WIDEMIND_BACKUP_EXTRA`. A configured database that is missing fails the backup.

To restore, stop the services, then copy each database out of a verified backup to a new path and swap it in:

```sh
npm run backup:verify -- --path /var/backups/widemind/<backup>
npm run backup:restore -- --path /var/backups/widemind/<backup> --label economy --to /var/lib/widemind-economy/economy.restored.sqlite3
```

Restore never overwrites an existing database or its `-wal`/`-shm` files. Restore a consistent set (all databases from the same backup), fix ownership, then start the services and run `npm run pilot:preflight`.

## Off-host encrypted copies

After each backup the timer encrypts it and uploads it to S3-compatible storage (Backblaze B2, Cloudflare R2, AWS S3 and similar). Each archive is encrypted with AES-256-GCM under a fresh key, and that key is sealed with your RSA public key. The server holds only the public key, so neither it nor the storage provider can read the archives.

1. On your own computer, not the server:
   ```sh
   npm run backup:keygen -- --private ~/widemind-backup-private.pem --public widemind-backup-public.pem
   ```
   Keep the private key off the server, with a second copy somewhere safe. Without it the off-host copies cannot be opened.
2. Copy the public key to `/etc/widemind/backup-public.pem`. Create a bucket and an access key limited to writing into it, and set the `WIDEMIND_OFFSITE_*` settings ([example](../deployment/widemind.env.example)). Turn on the bucket's object versioning or lifecycle rules for retention; WideMind never deletes uploaded copies.
3. `npm run backup && npm run backup:offsite` uploads the newest backup and records `offsite.json`. The `offsite_backup_stale` alert and the `offsite_backups` preflight check use it.

To restore from an off-host copy, download the `.wmbk` file with your storage provider's tools, then on your own computer:

```sh
npm run backup:decrypt -- --in <file>.wmbk --key ~/widemind-backup-private.pem --out <empty directory>
```

It refuses a tampered archive or the wrong key, and verifies the decrypted backup the same way as `backup:verify`. Restore the databases from that directory with `backup:restore -- --path <directory>`.

## Container (sandbox)

`Dockerfile` and `docker-compose.yml` run every service in one container behind Caddy, for trying the agent with test money on a host that has only Docker:

```sh
WIDEMIND_DOMAIN=agent.example.com docker compose up -d --build
docker compose logs app
```

The first start creates the agent identity, service tokens and owner password; the password is printed once in the logs. Data lives in the `widemind-data` volume. Portal-only mode is on by default (`WIDEMIND_PORTAL_ONLY`), so no Gmail account is needed. Use `WIDEMIND_DOMAIN=localhost` to try it on one machine.

This is not the pilot layout: the economy runs in the same container and under the same user as the agent, so it is not outside the agent's write access. Use the systemd steps above for live money. The container files have not yet been exercised on a Docker host; treat the first build as a test.

## Not covered

Host hardening, log retention and firewalling are left to the host. Run one alerts process per run.
