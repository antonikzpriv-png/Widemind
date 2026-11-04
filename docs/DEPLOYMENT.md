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
4. **Data.** `npm run migrate` as the `widemind` user. Create `/var/backups/widemind` as `root:widemind` mode 0750.
5. **Services.** Copy the unit files to `/etc/systemd/system/`, then:
   ```sh
   systemctl daemon-reload
   systemctl enable --now widemind-economy widemind-economy-worker widemind-tools widemind-business widemind-web widemind-worker widemind-alerts
   systemctl enable --now widemind-backup.timer widemind-snapshot.timer
   ```
   `npm run services:status` checks the loopback services and `npm run status` the worker.
6. **HTTPS.** Put `deployment/Caddyfile` (with your domain) in `/etc/caddy/Caddyfile` and reload Caddy. Only the web app is exposed.
7. **Accounts.** Register the PayPal webhook at `https://<domain>/api/modules/economy/webhook` and complete Gmail OAuth ([GMAIL.md](GMAIL.md)) and the OpenAI key ([AI-PROVIDERS.md](AI-PROVIDERS.md)).
8. **Sandbox rehearsal.** Open the run in sandbox (`npm run pilot:init -- --sandbox`), run one order end to end, and confirm alerts reach your webhook.
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

## Not covered

Off-host backup copies (sync `/var/backups/widemind` elsewhere), host hardening, log retention and firewalling are left to the host. Run one alerts process per run.
