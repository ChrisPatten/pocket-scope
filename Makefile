.PHONY: deploy pull-pi

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