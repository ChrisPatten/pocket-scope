Spatial utilities
=================

PocketScope provides a small, dependency-free spatial helper module at
`src/pocketscope/data/spatial.py` that implements common mapping operations
and minimal GeoJSON ↔ WKB conversion so geometries can be stored in SQLite
without an external spatial extension.

Key utilities
-------------

- haversine_miles(lat1, lon1, lat2, lon2) -> float
  - Great-circle distance in statute miles between two WGS‑84 points.

- approx_bbox_for_radius(lat, lon, radius_miles) -> (min_lon, min_lat, max_lon, max_lat)
  - Fast approximate lon/lat bounding box surrounding a given radius. Used
    for inexpensive spatial filtering prior to accurate distance checks.

- polygon_centroid(rings) -> (lon, lat)
  - Compute centroid of a polygon exterior (rings is a sequence of rings).

- geometry_to_wkb(geometry) -> bytes
  - Convert a GeoJSON-like geometry mapping (Point, Polygon, MultiPolygon)
    to a minimal WKB-like byte format used by the ingestion tool and DB.

- wkb_to_geometry(blob) -> dict
  - Read back the WKB bytes produced above into a GeoJSON-like dict.

- register_haversine(conn)
  - Register a SQL function `HAV_MILES(lat1, lon1, lat2, lon2)` on a
    sqlite3.Connection to enable distance calculations inside SQL queries.

WKB format notes
-----------------

This project uses a lightweight binary encoding for the small set of
geometries we need. The format is intentionally minimal and not a full
implementation of the OGC WKB spec; it is intended only to allow storing
and retrieving Points, Polygons and MultiPolygons in an SQLite BLOB column
without external dependencies.

Ingestion CLI
-------------

The repository includes a small CLI `pocketscope.data.ingest_geojson_to_sqlite`
that ingests three GeoJSON files (airports, runways, US states) into a
spatially indexed SQLite database. It performs light validation, converts
geometries to WKB, computes bounding boxes and centroids, and writes
insert-ready rows into the DB schema found beside the ingestion module.

Example
-------

```bash
python -m pocketscope.data.ingest_geojson_to_sqlite \
  --airports assets/airports.json \
  --runways assets/runways.json \
  --states assets/states.geojson \
  --out build/map.db --replace
```

The resulting `map.db` is suitable for the application's map provider
which queries nearby airports, runways and states by bounding box and
distance.

Notes and limitations
---------------------

- The WKB implementation here is intentionally minimal and optimized for
  simplicity and performance; it is not a full OGC-compliant implementation.
- The approximate bounding box is intentionally conservative and should be
  combined with an accurate haversine distance check for exact filtering.
