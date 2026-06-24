.PHONY: pi-deploy local-db local-log-demo help pi-logs pi-restart pi-settings \
	feeder-up feeder-down feeder-restart feeder-status feeder-logs feeder-save

# Device host & log settings (override: `make pi-logs PI_HOST=host LOG_LINES=200`)
PI_HOST ?= pocketscope.local
LOG_LINES ?= 50

# Templated systemd unit on the device: pocketscope@<PI_USER>.service
PI_USER ?= pocketscope
PI_SERVICE ?= pocketscope@$(PI_USER).service

# Python interpreter for local tasks (override: `make db PYTHON=python3`)
PYTHON ?= .venv/bin/python

# Mac-side feeder (pm2) settings
FEEDER_APPS ?= readsb aircraft-json-server
FEEDER_LINES ?= 50

## Show available make targets and their descriptions
help:
	@echo "PocketScope Make targets:";
	@awk -F':' ' \
		/^[a-zA-Z0-9_.-]+:/ { \
			gsub(/:.*/, "", $$1); tgt=$$1; \
			if (prev ~ /^##/) { \
				gsub(/^##[ ]?/, "", prev); \
				printf "  %-16s %s\n", tgt, prev; \
			} \
		} { prev=$$0 }' $(MAKEFILE_LIST)

## Rsync code to device and restart systemd service
pi-deploy:
	@echo "Deploying to $(PI_HOST)..."
	rsync -avR --exclude-from='.rsync-exclude' . $(PI_HOST):~/pocket-scope
	ssh $(PI_HOST) 'sudo systemctl restart $(PI_SERVICE)'

## Build or replace local SQLite geo database
local-db:
	$(PYTHON) -m pocketscope.data.ingest_geojson_to_sqlite \
		--airports src/pocketscope/assets/airports.json \
		--runways src/pocketscope/assets/runways.json \
		--states src/pocketscope/assets/us_states.json \
		--out ~/.pocketscope/pocketscope.db --replace

## Run logging demo tool
local-log-demo:
	$(PYTHON) -m pocketscope.tools.log_demo

## Tail systemd service logs on device via SSH
pi-logs:
	@echo "Tailing $(PI_SERVICE) logs from $(PI_HOST)... (Ctrl-C to exit)"
	ssh $(PI_HOST) 'journalctl -u $(PI_SERVICE) -f -n $(LOG_LINES)'

## Restart the service on the device
pi-restart:
	@echo "Restarting $(PI_SERVICE) on $(PI_HOST)..."
	ssh $(PI_HOST) 'sudo systemctl restart $(PI_SERVICE)'

## Copy ./bootstrap_assets/settings.yml to device
pi-settings:
	@echo "Copying settings.yml to $(PI_HOST)..."
	scp ./bootstrap_assets/settings.yml $(PI_HOST):~/.pocketscope/settings.yml

## Start the Mac-side feeder (readsb + json server) under pm2
feeder-up:
	pm2 start ecosystem.config.js

## Stop the Mac-side feeder pm2 apps
feeder-down:
	pm2 stop $(FEEDER_APPS)

## Restart the Mac-side feeder pm2 apps
feeder-restart:
	pm2 restart $(FEEDER_APPS)

## Show pm2 status (all apps)
feeder-status:
	pm2 status

## Tail Mac-side feeder logs (override: make feeder-logs FEEDER_LINES=200)
feeder-logs:
	pm2 logs $(FEEDER_APPS) --lines $(FEEDER_LINES)

## Persist current pm2 process list for reboot resurrection
feeder-save:
	pm2 save