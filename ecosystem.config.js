// ecosystem.config.js — pm2 definitions for the Mac-side ADS-B feeder.
//
// Manages the two long-running processes that run on the Mac (the box with the
// RTL-SDR) and feed the Raspberry Pi running PocketScope:
//   1. readsb                 — RTL-SDR decoder writing aircraft.json
//   2. aircraft-json-server   — static HTTP server serving that JSON to the Pi
//
// Replaces the screen-based ~/workspace/start_pocketscope.sh for these two
// processes. The PocketScope app itself runs on the Pi via systemd and is not
// managed here.
//
// Usage:
//   npm install -g pm2
//   pm2 start ecosystem.config.js
//   pm2 save && pm2 startup     # boot persistence via launchd
//
// pm2 launches children with a minimal environment, so absolute interpreter
// paths are used (no reliance on the pyenv/Homebrew PATH). Override any of the
// paths/coords below via the matching environment variables if needed.
const os = require('os')
const path = require('path')

const HOME = os.homedir()
const READSB_DIR = process.env.READSB_DIR || path.join(HOME, 'workspace/readsb/html')
const READSB_DATA = path.join(READSB_DIR, 'data')
const READSB_BIN = process.env.READSB_BIN || '/opt/homebrew/bin/readsb'
const PYTHON_BIN = process.env.PYTHON_BIN || '/Users/chrispatten/.pyenv/versions/3.11.2/bin/python3'

const LAT = process.env.READSB_LAT || '42.00748'
const LON = process.env.READSB_LON || '-71.20899'

module.exports = {
  apps: [
    {
      name: 'readsb',
      script: READSB_BIN,
      interpreter: 'none',
      // No --interactive: pm2 runs detached with no TTY; output goes to logs.
      args: [
        '--quiet', '--net',
        '--device-type', 'rtlsdr',
        '--gain', 'auto',
        '--lat', LAT, '--lon', LON,
        '--write-json', READSB_DATA,
        '--write-json-every', '1',
        '--json-location-accuracy', '2',
      ],
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
    },
    {
      name: 'aircraft-json-server',
      script: PYTHON_BIN,
      interpreter: 'none',
      args: ['-m', 'http.server', '8080', '--bind', '0.0.0.0', '--directory', READSB_DIR],
      autorestart: true,
      restart_delay: 3000,
    },
  ],
}
