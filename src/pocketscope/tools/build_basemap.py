from pathlib import Path
from pocketscope.data.base_map import BaseMap

db = str(Path.home() / ".pocketscope" / "basemap.sqlite")
bm = BaseMap(db)

# ingest sources (adjust paths if needed)
bm.ingest_runways("src/pocketscope/assets/runways.json")
bm.ingest_states("src/pocketscope/assets/us_states.json")
bm.ingest_airports("src/pocketscope/assets/airports.geojson")

print("basemap built at:", db)
