-- osm_flex.lua: osm2pgsql flex config for environmental/spatial analysis.
--
-- Imports into schema 'osm', three tables:
--   osm.osm_points   — node-level features (small airports, abattoirs, etc.)
--   osm.osm_lines    — major highways (motorway / trunk / primary)
--   osm.osm_polygons — area features (green space, industrial, landfill, military, airports)
--
-- feature_type values:
--   green_space, landfill, industrial, env_hazard, military, abattoir,
--   airport, highway_motorway, highway_trunk, highway_primary

local tables = {}

tables.points = osm2pgsql.define_table({
    name   = 'osm_points',
    schema = 'osm',
    ids    = { type = 'node', id_column = 'osm_id' },
    columns = {
        { column = 'feature_type', type = 'text',     not_null = true },
        { column = 'name',         type = 'text' },
        { column = 'tags',         type = 'jsonb' },
        { column = 'geom',         type = 'point',    projection = 4326 },
    }
})

tables.lines = osm2pgsql.define_table({
    name   = 'osm_lines',
    schema = 'osm',
    ids    = { type = 'way', id_column = 'osm_id' },
    columns = {
        { column = 'feature_type', type = 'text',       not_null = true },
        { column = 'name',         type = 'text' },
        { column = 'tags',         type = 'jsonb' },
        { column = 'geom',         type = 'linestring', projection = 4326 },
    }
})

tables.polygons = osm2pgsql.define_table({
    name   = 'osm_polygons',
    schema = 'osm',
    ids    = { type = 'area', id_column = 'osm_id' },
    columns = {
        { column = 'feature_type', type = 'text',     not_null = true },
        { column = 'name',         type = 'text' },
        { column = 'tags',         type = 'jsonb' },
        { column = 'geom',         type = 'geometry', projection = 4326 },
    }
})

-- Returns a feature_type string, or nil to skip this object.
-- More-specific checks come first so abattoirs aren't caught by 'industrial'.
local function classify(tags)
    local landuse    = tags.landuse
    local leisure    = tags.leisure
    local natural    = tags.natural
    local aeroway    = tags.aeroway
    local highway    = tags.highway
    local military   = tags.military
    local industrial = tags.industrial
    local man_made   = tags.man_made

    -- Highways (open ways only; classification happens in process_way)
    if highway == 'motorway' then return 'highway_motorway' end
    if highway == 'trunk'    then return 'highway_trunk'    end
    if highway == 'primary'  then return 'highway_primary'  end

    -- Airports
    if aeroway == 'aerodrome' or aeroway == 'airstrip' then return 'airport' end

    -- Military
    if landuse == 'military' or military == 'base' or military == 'airfield' then
        return 'military'
    end

    -- Abattoirs — more specific than generic industrial, check first
    if industrial == 'slaughterhouse' then return 'abattoir' end

    -- Landfill
    if landuse == 'landfill' then return 'landfill' end

    -- Environmental hazards
    if landuse == 'brownfield' or landuse == 'quarry' then return 'env_hazard' end
    if industrial == 'chemical'  or industrial == 'refinery'
    or industrial == 'oil'       or industrial == 'mine' then return 'env_hazard' end

    -- Industrial (generic — after more-specific checks above)
    if landuse   == 'industrial'                    then return 'industrial' end
    if man_made  == 'works'                         then return 'industrial' end
    if industrial == 'factory'   or industrial == 'scrap_yard'
    or industrial == 'warehouse' then return 'industrial' end

    -- Green spaces
    if landuse == 'forest'            or landuse == 'grass'
    or landuse == 'recreation_ground' or landuse == 'village_green'
    or landuse == 'greenery'          then return 'green_space' end
    if leisure == 'park'        or leisure == 'nature_reserve'
    or leisure == 'garden'      or leisure == 'common' then return 'green_space' end
    if natural == 'wood'        or natural == 'scrub'
    or natural == 'wetland'     or natural == 'grassland'
    or natural == 'heath'       then return 'green_space' end

    return nil
end

-- Subset of tags worth keeping for downstream queries.
local function pick_tags(tags)
    return {
        landuse    = tags.landuse,
        leisure    = tags.leisure,
        natural    = tags.natural,
        aeroway    = tags.aeroway,
        highway    = tags.highway,
        military   = tags.military,
        industrial = tags.industrial,
        man_made   = tags.man_made,
        amenity    = tags.amenity,
    }
end

function osm2pgsql.process_node(object)
    local ft = classify(object.tags)
    if not ft then return end
    -- Highways are never meaningful as isolated nodes
    if ft:sub(1, 7) == 'highway' then return end
    tables.points:insert({
        feature_type = ft,
        name         = object.tags.name,
        tags         = pick_tags(object.tags),
        geom         = object:as_point(),
    })
end

function osm2pgsql.process_way(object)
    local ft = classify(object.tags)
    if not ft then return end

    if ft:sub(1, 7) == 'highway' then
        -- Open ways: highways go into the lines table
        tables.lines:insert({
            feature_type = ft,
            name         = object.tags.name,
            tags         = pick_tags(object.tags),
            geom         = object:as_linestring(),
        })
    elseif object.is_closed then
        -- Closed ways: area features go into the polygons table
        tables.polygons:insert({
            feature_type = ft,
            name         = object.tags.name,
            tags         = pick_tags(object.tags),
            geom         = object:as_polygon(),
        })
    end
end

function osm2pgsql.process_relation(object)
    -- Only handle multipolygon relations (e.g. large parks, complex industrial sites)
    if object.tags.type ~= 'multipolygon' then return end
    local ft = classify(object.tags)
    if not ft then return end
    local geom = object:as_multipolygon()
    if not geom then return end
    tables.polygons:insert({
        feature_type = ft,
        name         = object.tags.name,
        tags         = pick_tags(object.tags),
        geom         = geom,
    })
end
