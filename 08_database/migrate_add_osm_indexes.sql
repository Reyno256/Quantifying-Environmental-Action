-- Post-import indexes for OSM tables.
-- Run after import_osm.sh completes (osm2pgsql creates the tables; this adds indexes).

CREATE INDEX IF NOT EXISTS osm_points_geom_idx   ON osm.osm_points   USING GIST (geom);
CREATE INDEX IF NOT EXISTS osm_lines_geom_idx    ON osm.osm_lines    USING GIST (geom);
CREATE INDEX IF NOT EXISTS osm_polygons_geom_idx ON osm.osm_polygons USING GIST (geom);

CREATE INDEX IF NOT EXISTS osm_points_type_idx   ON osm.osm_points   (feature_type);
CREATE INDEX IF NOT EXISTS osm_lines_type_idx    ON osm.osm_lines    (feature_type);
CREATE INDEX IF NOT EXISTS osm_polygons_type_idx ON osm.osm_polygons (feature_type);

ANALYZE osm.osm_points;
ANALYZE osm.osm_lines;
ANALYZE osm.osm_polygons;
