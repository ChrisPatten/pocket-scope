# Running the ADS-B Feeder with pm2

This guide covers the **feeder** side of PocketScope: the machine with the
RTL-SDR dongle that decodes ADS-B and serves `aircraft.json` over HTTP. The
PocketScope display app (on the Raspberry Pi) polls that JSON — see
[systemd-setup.md](systemd-setup.md) for the display side.

The feeder runs two long-lived processes, managed by
[pm2](https://pm2.keymetrics.io/) instead of ad-hoc `screen` sessions so they
get crash auto-restart, boot persistence, and unified logs:

1. **readsb** — RTL-SDR decoder, writes `aircraft.json` to readsb's `data/` dir.
2. **aircraft-json-server** — `python -m http.server` serving readsb's `html/`
   dir so the Pi can fetch `aircraft.json`.

Process definitions live in [`ecosystem.config.js`](../ecosystem.config.js) at
the repo root, so the feeder is configured in one versioned file.

---

## 1. Prerequisites

```bash
# RTL-SDR decoder (Homebrew on macOS, or your platform's package)
brew install readsb        # provides /opt/homebrew/bin/readsb

# pm2 (requires Node.js, which Homebrew installs as a dependency)
npm install -g pm2
```

---

## 2. Configuration

`ecosystem.config.js` uses absolute interpreter paths (pm2 launches children
with a minimal environment that does not include the pyenv/Homebrew `PATH`).
Override any of these via environment variables when starting pm2:

| Variable      | Default                                              | Purpose                          |
|---------------|------------------------------------------------------|----------------------------------|
| `READSB_DIR`  | `~/workspace/readsb/html`                            | readsb web/data root             |
| `READSB_BIN`  | `/opt/homebrew/bin/readsb`                           | readsb binary                    |
| `PYTHON_BIN`  | absolute pyenv python (`pyenv which python3`)        | interpreter for the HTTP server  |
| `READSB_LAT`  | `42.00748`                                           | receiver latitude                |
| `READSB_LON`  | `-71.20899`                                          | receiver longitude               |

Update `PYTHON_BIN` in the file to the output of `pyenv which python3` if your
Python version changes.

---

## 3. Start the feeder

Using the Makefile (preferred):

```bash
make feeder-up        # pm2 start ecosystem.config.js
make feeder-status    # show pm2 process table
make feeder-logs      # tail readsb + json server logs
```

Or directly with pm2:

```bash
pm2 start ecosystem.config.js
pm2 status
pm2 logs readsb
```

Verify the JSON endpoint is live (and reachable from the Pi at the feeder's
LAN IP):

```bash
curl -s http://localhost:8080/data/aircraft.json | head
```

---

## 4. Boot persistence (launchd)

```bash
make feeder-save      # pm2 save — snapshot the running process list
pm2 startup           # prints a sudo command — run it to install the launchd agent
```

`pm2 startup` detects launchd on macOS and prints a `sudo env PATH=... pm2
startup launchd ...` command; run that once. On reboot, launchd relaunches pm2
and `pm2 save`'s snapshot resurrects the processes.

---

## 5. Make targets

All feeder targets wrap pm2 and are prefixed `feeder-` so it is clear they run
on the feeder machine (see also `local-*` for dev tasks and `pi-*` for the
display device):

| Target                | Action                                          |
|-----------------------|-------------------------------------------------|
| `make feeder-up`      | `pm2 start ecosystem.config.js`                 |
| `make feeder-down`    | Stop the feeder apps                             |
| `make feeder-restart` | Restart the feeder apps                          |
| `make feeder-status`  | `pm2 status`                                     |
| `make feeder-logs`    | Tail logs (`FEEDER_LINES=200` to override)      |
| `make feeder-save`    | Persist the process list for reboot             |

App names and log depth are configurable via `FEEDER_APPS` and `FEEDER_LINES`.

---

## 6. Troubleshooting

- **readsb crash-loops / "device busy"**: another readsb already holds the SDR
  (e.g. a leftover `screen` session). Stop it first:
  `pgrep -fl readsb` then `kill <pid>`, or `screen -ls` / `screen -S readsb -X quit`.
- **HTTP server fails to bind 8080**: another process owns the port —
  `lsof -i :8080`.
- **`pm2` nags that the in-memory daemon is out of date**: run `pm2 update`,
  but note it reloads the daemon and restarts **all** pm2-managed apps.
- **Wrong Python after a pyenv version bump**: refresh `PYTHON_BIN` in
  `ecosystem.config.js` with `pyenv which python3`.
