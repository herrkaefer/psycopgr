# psycopgr

Python client for finding routes and travel costs in a PostgreSQL routing graph.

## Requirements

- Python 3.11 or later with Psycopg 3.
- Reference database stack: PostgreSQL 18, PostGIS 3.7, pgRouting 4.0.
- A graph with edge and vertex tables. For OpenStreetMap data, use osm2pgrouting 3.0 or an existing osm2pgrouting 2.x import.

## Install

```sh
pip install 'psycopgr[binary]'
```

For this checkout, use `pip install '.[binary]'`. The base package uses your system's `libpq`; the `binary` extra bundles it on supported platforms.

## Load a graph

In a database where the extensions are available, enable them once as a database administrator:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;
```

For a new OpenStreetMap graph, follow the [osm2pgrouting 3.0 installation instructions](https://github.com/pgRouting/osm2pgrouting/tree/v3.0.0), then import an OSM XML extract using its supplied map configuration:

```sh
osm2pgrouting --file region.osm --conf /path/to/mapconfig.xml \
  --dbname routing --host localhost --username routing
```

You can also use a graph you prepared yourself. `set_meta_data()` accepts schema-qualified `table` and `vertex_table` names. If you omit `vertex_table`, the library uses `<table>_vertices_pgr` in the same schema. The vertex table needs an `id` and a point geometry column with the configured SRID. A GiST index on the point column speeds up snapping.

## Route between coordinates

```python
from psycopgr import PGRouting, PgrNode

start = PgrNode("start", -87.630, 41.881)
end = PgrNode("end", -87.623, 41.887)

with PGRouting("host=localhost dbname=routing user=routing") as routing:
    # Use these overrides for tables created by osm2pgrouting 3.
    routing.set_meta_data(id="id", geometry="geom", vertex_geometry="geom")

    routes = routing.get_routes(start, end, gpx_file="route.gpx")
    costs = routing.get_costs(start, end)
```

The default names match osm2pgrouting 2.x: `ways.gid` and `ways_vertices_pgr.the_geom`.
Edge columns default to `source`, `target`, `cost_s`, and `reverse_cost_s`.
Set `has_reverse_cost=False` for a graph without the reverse cost column.
`astar()` also expects edge coordinate columns `x1`, `y1`, `x2`, and `y2`.

`PgrNode(id, lon, lat)` uses longitude and latitude in WGS84 by default. For a projected graph,
set `srid` and pass the graph's X/Y coordinates in those fields. GPX exports use WGS84.
Each endpoint argument can be a single node or an iterable of nodes. Results are keyed by
`(start_node, end_node)`. Each route contains a `path` and a `cost`. Unreachable pairs and
identical endpoints are omitted. The cost combines graph travel time with the time it takes
to reach the graph at `end_speed` (default 10 km/h). Use seconds for the edge costs when
routing from coordinates.

`dijkstra()`, `dijkstra_cost()`, and `astar()` work directly with vertex IDs. With the 2.0
client, database errors propagate as Psycopg exceptions. Use the context manager or call
`close()` when finished. For an existing database, test the PostgreSQL and extension upgrade
on a restored backup before migrating live data.

## Develop and test

Install [uv](https://docs.astral.sh/uv/) and Docker with Compose, then run:

```sh
uv sync --locked
docker compose up --build --wait
PYTHONWARNINGS=error PSYCOPGR_TEST_DSN=postgresql://routing:routing@127.0.0.1:55432/routing \
  uv run --locked --no-sync coverage run --branch --source=psycopgr \
  -m unittest discover -s tests -p test_routing_integration.py -v
uv run --locked coverage report --show-missing --fail-under=95
uv build
uv run --locked twine check --strict dist/*
docker compose down --volumes
```

The integration suite creates a temporary schema and requires permission to enable the two
extensions if they are absent. The Compose database is for local development.

CI runs the routing suite on Python 3.11–3.14, treats Python and database warnings as test
failures, and enforces at least 95% branch and statement coverage combined. A separate job
imports an OSM fixture with osm2pgrouting 3.0 and routes across the imported graph.
