.PHONY: deploy pull-pi db log-demo

deploy:
	@echo "Deploying to pocketscope.local..."
	rsync -avR --exclude-from='.rsync-exclude' . pocketscope.local:~/pocket-scope
	ssh pocketscope.local 'sudo systemctl restart pocketscope.service'

pull-pi:
	@echo "Pulling pi-display branch on pocketscope.local..."
	cd ~/pocket-scope && \
		git fetch origin && \
		git checkout pi-display && \
		git pull origin pi-display && \
		sudo systemctl restart pocketscope.service

db:
	python -m pocketscope.data.ingest_geojson_to_sqlite \
		--airports src/pocketscope/assets/airports.json \
		--runways src/pocketscope/assets/runways.json \
		--states src/pocketscope/assets/us_states.json \
		--out ~/.pocketscope/pocketscope.db --replace

log-demo:
	python -m pocketscope.tools.log_demo
