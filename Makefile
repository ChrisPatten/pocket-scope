.PHONY: deploy pull-pi db log-demo help logs

# Device host & log settings (override: `make logs PI_HOST=host LOG_LINES=200`)
PI_HOST ?= pocketscope.local
LOG_LINES ?= 50

## Show available make targets and their descriptions
help:
	@echo "PocketScope Make targets:";
	@awk -F':' ' \
		/^[a-zA-Z0-9_.-]+:/ { \
			gsub(/:.*/, "", $$1); tgt=$$1; \
			if (prev ~ /^##/) { \
				gsub(/^##[ ]?/, "", prev); \
				printf "  %-14s %s\n", tgt, prev; \
			} \
		} { prev=$$0 }' $(MAKEFILE_LIST)

## Rsync code to device and restart systemd service
deploy:
	@echo "Deploying to $(PI_HOST)..."
	rsync -avR --exclude-from='.rsync-exclude' . $(PI_HOST):~/pocket-scope
	ssh $(PI_HOST) 'sudo systemctl restart pocketscope@pocketscope.service'

## Update pi-display branch on device and restart service
pull-pi:
	@echo "Pulling pi-display branch on $(PI_HOST)..."
	cd ~/pocket-scope && \
		git fetch origin && \
		git checkout pi-display && \
		git pull origin pi-display && \
		sudo systemctl restart pocketscope@pocketscope.service

## Build or replace local SQLite geo database
db:
	python -m pocketscope.data.ingest_geojson_to_sqlite \
		--airports src/pocketscope/assets/airports.json \
		--runways src/pocketscope/assets/runways.json \
		--states src/pocketscope/assets/us_states.json \
		--out ~/.pocketscope/pocketscope.db --replace

## Run logging demo tool
log-demo:
	python -m pocketscope.tools.log_demo

## Tail systemd service logs on device via SSH
logs:
	@echo "Tailing pocketscope@pocketscope.service logs from $(PI_HOST)... (Ctrl-C to exit)"
	ssh $(PI_HOST) 'journalctl -u pocketscope@pocketscope.service -f -n $(LOG_LINES)'

## Restart the service on the device
pi-restart:
	@echo "Restarting pocketscope@pocketscope.service on $(PI_HOST)..."
	ssh $(PI_HOST) 'sudo systemctl restart pocketscope@pocketscope.service'

## Copy ./bootstrap_assets/settings.yml to device
pi-settings:
	@echo "Copying settings.yml to $(PI_HOST)..."
	scp ./bootstrap_assets/settings.yml $(PI_HOST):~/.pocketscope/settings.yml