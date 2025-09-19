PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;

-- Airports
CREATE TABLE IF NOT EXISTS airports (
  id TEXT PRIMARY KEY,
  ident TEXT,
  name TEXT,
  icao_id TEXT,
  type_code TEXT,
  servcity TEXT,
  state TEXT,
  country TEXT,
  elevation_ft REAL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  geom_wkb BLOB NOT NULL,
  bbox_minx REAL NOT NULL,
  bbox_miny REAL NOT NULL,
  bbox_maxx REAL NOT NULL,
  bbox_maxy REAL NOT NULL
);

-- Runways
CREATE TABLE IF NOT EXISTS runways (
  id TEXT PRIMARY KEY,
  airport_id TEXT NOT NULL,
  designator TEXT,
  length_ft INTEGER,
  width_ft INTEGER,
  surface TEXT,
  light_actv INTEGER,
  light_intns TEXT,
  geom_wkb BLOB NOT NULL,
  centroid_lat REAL NOT NULL,
  centroid_lon REAL NOT NULL,
  bbox_minx REAL NOT NULL,
  bbox_miny REAL NOT NULL,
  bbox_maxx REAL NOT NULL,
  bbox_maxy REAL NOT NULL,
  FOREIGN KEY (airport_id) REFERENCES airports(id) ON DELETE CASCADE
);

-- US States
CREATE TABLE IF NOT EXISTS us_states (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  geom_wkb BLOB NOT NULL,
  bbox_minx REAL NOT NULL,
  bbox_miny REAL NOT NULL,
  bbox_maxx REAL NOT NULL,
  bbox_maxy REAL NOT NULL
);

-- R*Trees for fast bbox queries
CREATE VIRTUAL TABLE IF NOT EXISTS airports_rtree USING rtree(
  rowid, minx, maxx, miny, maxy
);
CREATE VIRTUAL TABLE IF NOT EXISTS runways_rtree USING rtree(
  rowid, minx, maxx, miny, maxy
);
CREATE VIRTUAL TABLE IF NOT EXISTS us_states_rtree USING rtree(
  rowid, minx, maxx, miny, maxy
);

-- Triggers to maintain rtrees
CREATE TRIGGER IF NOT EXISTS airports_ai AFTER INSERT ON airports BEGIN
  INSERT INTO airports_rtree(rowid, minx, maxx, miny, maxy)
  VALUES (new.rowid, new.bbox_minx, new.bbox_maxx, new.bbox_miny, new.bbox_maxy);
END;
CREATE TRIGGER IF NOT EXISTS airports_ad AFTER DELETE ON airports BEGIN
  DELETE FROM airports_rtree WHERE rowid = old.rowid;
END;
CREATE TRIGGER IF NOT EXISTS airports_au AFTER UPDATE ON airports BEGIN
  UPDATE airports_rtree SET minx=new.bbox_minx, maxx=new.bbox_maxx, miny=new.bbox_miny, maxy=new.bbox_maxy
  WHERE rowid=new.rowid;
END;

CREATE TRIGGER IF NOT EXISTS runways_ai AFTER INSERT ON runways BEGIN
  INSERT INTO runways_rtree(rowid, minx, maxx, miny, maxy)
  VALUES (new.rowid, new.bbox_minx, new.bbox_maxx, new.bbox_miny, new.bbox_maxy);
END;
CREATE TRIGGER IF NOT EXISTS runways_ad AFTER DELETE ON runways BEGIN
  DELETE FROM runways_rtree WHERE rowid = old.rowid;
END;
CREATE TRIGGER IF NOT EXISTS runways_au AFTER UPDATE ON runways BEGIN
  UPDATE runways_rtree SET minx=new.bbox_minx, maxx=new.bbox_maxx, miny=new.bbox_miny, maxy=new.bbox_maxy
  WHERE rowid=new.rowid;
END;

CREATE TRIGGER IF NOT EXISTS us_states_ai AFTER INSERT ON us_states BEGIN
  INSERT INTO us_states_rtree(rowid, minx, maxx, miny, maxy)
  VALUES (new.rowid, new.bbox_minx, new.bbox_maxx, new.bbox_miny, new.bbox_maxy);
END;
CREATE TRIGGER IF NOT EXISTS us_states_ad AFTER DELETE ON us_states BEGIN
  DELETE FROM us_states_rtree WHERE rowid = old.rowid;
END;
CREATE TRIGGER IF NOT EXISTS us_states_au AFTER UPDATE ON us_states BEGIN
  UPDATE us_states_rtree SET minx=new.bbox_minx, maxx=new.bbox_maxx, miny=new.bbox_miny, maxy=new.bbox_maxy
  WHERE rowid=new.rowid;
END;

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_runways_airport_id ON runways(airport_id);
CREATE INDEX IF NOT EXISTS idx_airports_ident ON airports(ident);
