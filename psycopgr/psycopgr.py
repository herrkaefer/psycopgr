from collections import namedtuple
from itertools import product
from math import isfinite
from pathlib import Path
from xml.etree import ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


PgrNode = namedtuple("PgrNode", ["id", "lon", "lat"])


class PGRouting:
    """Route between coordinates using a PostgreSQL routing graph."""

    _defaults = {
        "table": "ways",
        "vertex_table": None,
        "id": "gid",
        "source": "source",
        "target": "target",
        "cost": "cost_s",
        "reverse_cost": "reverse_cost_s",
        "x1": "x1",
        "y1": "y1",
        "x2": "x2",
        "y2": "y2",
        "geometry": "the_geom",
        "vertex_geometry": "the_geom",
        "has_reverse_cost": True,
        "directed": True,
        "srid": 4326,
    }

    def __init__(self, conninfo="", **kwargs):
        """Accept a PostgreSQL connection string or Psycopg connection options."""
        if "database" in kwargs:
            kwargs.setdefault("dbname", kwargs.pop("database"))
        self._meta_data = self._defaults.copy()
        kwargs.setdefault("autocommit", True)
        self._conn = psycopg.connect(conninfo, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        """Close the database connection."""
        self._conn.close()

    def set_meta_data(self, **kwargs):
        """Override graph names and options for this connection."""
        for key, value in kwargs.items():
            if key not in self._defaults:
                raise ValueError(f"Unknown graph option: {key}")
            if key in {"directed", "has_reverse_cost"}:
                valid = isinstance(value, bool)
            elif key == "srid":
                valid = type(value) is int and value > 0
            elif key == "vertex_table" and value is None:
                valid = True
            elif key in {"table", "vertex_table"}:
                valid = isinstance(value, str) and all(value.split(".")) and value.count(".") <= 1
            else:
                valid = isinstance(value, str) and bool(value)
            if not valid:
                raise ValueError(f"Invalid value for {key}: {value!r}")
        self._meta_data.update(kwargs)
        return self._meta_data.copy()

    def _vertex_table(self):
        table = self._meta_data["vertex_table"] or self._meta_data["table"] + "_vertices_pgr"
        return sql.Identifier(*table.split("."))

    def _fetch(self, query, params):
        with self._conn.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(query, params).fetchall()

    def _edge_query(self, *, astar=False):
        columns = ["id", "source", "target", "cost"]
        if self._meta_data["has_reverse_cost"]:
            columns.append("reverse_cost")
        if astar:
            columns.extend(("x1", "y1", "x2", "y2"))
        fields = [
            sql.SQL("{} AS {}").format(sql.Identifier(self._meta_data[key]), sql.Identifier(key))
            for key in columns
        ]
        return sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(fields), sql.Identifier(*self._meta_data["table"].split("."))
        ).as_string(self._conn)

    def find_nearest_vertices(self, nodes):
        """Return a nearest vertex per input coordinate, or None for an empty graph."""
        geom = sql.Identifier(self._meta_data["vertex_geometry"])
        query = sql.SQL("""
            SELECT id, ST_X({geom}::geometry) AS lon, ST_Y({geom}::geometry) AS lat
            FROM {table}
            WHERE {geom} IS NOT NULL
            ORDER BY {geom} <-> ST_SetSRID(ST_MakePoint(%s, %s), %s), id
            LIMIT 1
        """).format(geom=geom, table=self._vertex_table())
        output = []
        for node in nodes:
            rows = self._fetch(query, (node.lon, node.lat, self._meta_data["srid"]))
            output.append(PgrNode(**rows[0]) if rows else None)
        return output

    def node_distance(self, node1, node2):
        """Return spheroidal distance in meters between two coordinates."""
        srid = self._meta_data["srid"]
        rows = self._fetch("""
            SELECT ST_Distance(
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), %s), 4326)::geography,
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), %s), 4326)::geography
            ) AS distance
        """, (node1.lon, node1.lat, srid, node2.lon, node2.lat, srid))
        return rows[0]["distance"]

    def dijkstra_cost(self, start_vids, end_vids):
        """Return costs keyed by vertex ID pair for reachable routes."""
        rows = self._fetch("""
            SELECT start_vid, end_vid, agg_cost
            FROM pgr_dijkstraCost(%s::text, %s::bigint[], %s::bigint[], %s::boolean)
        """, (self._edge_query(), list(start_vids), list(end_vids), self._meta_data["directed"]))
        return {(row["start_vid"], row["end_vid"]): row["agg_cost"] for row in rows}

    def _paths(self, call, params):
        geom = sql.Identifier(self._meta_data["vertex_geometry"])
        query = sql.SQL("""
            SELECT r.start_vid, r.end_vid, r.node, r.agg_cost,
                   ST_X(v.{geom}::geometry) AS lon, ST_Y(v.{geom}::geometry) AS lat
            FROM {call} AS r
            JOIN {table} AS v ON v.id = r.node
            ORDER BY r.seq
        """).format(call=call, table=self._vertex_table(), geom=geom)
        routes = {}
        for row in self._fetch(query, params):
            key = (row["start_vid"], row["end_vid"])
            route = routes.setdefault(key, {"path": [], "cost": 0.0})
            route["path"].append(PgrNode(row["node"], row["lon"], row["lat"]))
            route["cost"] = row["agg_cost"]
        return routes

    def dijkstra(self, start_vids, end_vids):
        """Return paths and costs keyed by vertex ID pair."""
        return self._paths(
            sql.SQL("pgr_dijkstra(%s::text, %s::bigint[], %s::bigint[], %s::boolean)"),
            (self._edge_query(), list(start_vids), list(end_vids), self._meta_data["directed"]),
        )

    def astar(self, start_vid, end_vid):
        """Return a path and cost keyed by vertex ID pair."""
        # Coordinate units need not match edge costs, so use the zero heuristic.
        return self._paths(
            sql.SQL("pgr_aStar(%s::text, %s::bigint, %s::bigint, directed => %s::boolean, heuristic => 0)"),
            (self._edge_query(astar=True), start_vid, end_vid, self._meta_data["directed"]),
        )

    def _route_coordinates(self, start_nodes, end_nodes, end_speed, *, paths):
        if not isfinite(end_speed) or end_speed <= 0:
            raise ValueError("end_speed must be a positive finite speed in km/h")
        starts = [start_nodes] if isinstance(start_nodes, PgrNode) else list(start_nodes)
        ends = [end_nodes] if isinstance(end_nodes, PgrNode) else list(end_nodes)
        if not starts or not ends:
            return {}
        nodes = list(dict.fromkeys(starts + ends))
        vertices = {
            node: vertex for node, vertex in zip(nodes, self.find_nearest_vertices(nodes))
            if vertex is not None
        }
        speed_mps = end_speed / 3.6
        offsets = {node: self.node_distance(node, vertex) / speed_mps
                   for node, vertex in vertices.items()}
        start_ids = list(dict.fromkeys(vertices[node].id for node in starts if node in vertices))
        end_ids = list(dict.fromkeys(vertices[node].id for node in ends if node in vertices))
        if not start_ids or not end_ids:
            return {}
        main_routes = (self.dijkstra if paths else self.dijkstra_cost)(start_ids, end_ids)
        result = {}
        for start, end in product(starts, ends):
            if start == end or start not in vertices or end not in vertices:
                continue
            first, last = vertices[start], vertices[end]
            if first.id == last.id:
                cost, path = 0.0, [first]
            else:
                route = main_routes.get((first.id, last.id))
                if route is None:
                    continue
                cost, path = (route["cost"], route["path"]) if paths else (route, [])
            total = cost + offsets[start] + offsets[end]
            result[start, end] = {"path": [start, *path, end], "cost": total} if paths else total
        return result

    def get_routes(self, start_nodes, end_nodes, end_speed=10.0, gpx_file=None):
        """Return paths and costs for reachable coordinate pairs."""
        routes = self._route_coordinates(start_nodes, end_nodes, end_speed, paths=True)
        if gpx_file is not None:
            self.get_gpx(routes, gpx_file)
        return routes

    def get_costs(self, start_nodes, end_nodes, end_speed=10.0):
        """Return costs for reachable coordinate pairs without building paths."""
        return self._route_coordinates(start_nodes, end_nodes, end_speed, paths=False)

    def get_gpx(self, routes, gpx_file=None):
        """Return GPX 1.1 and optionally write it to a file."""
        srid = self._meta_data["srid"]
        namespace = "http://www.topografix.com/GPX/1/1"
        root = ET.Element("gpx", xmlns=namespace, version="1.1", creator="psycopgr")
        for (start, end), route in routes.items():
            track = ET.SubElement(root, "trk")
            ET.SubElement(track, "name").text = (
                f"{start.lon},{start.lat}->{end.lon},{end.lat}: {route.get('cost')}"
            )
            segment = ET.SubElement(track, "trkseg")
            for node in route["path"]:
                lon, lat = node.lon, node.lat
                if srid != 4326:
                    rows = self._fetch("""
                        SELECT ST_X(point) AS lon, ST_Y(point) AS lat
                        FROM ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), %s), 4326) AS point
                    """, (lon, lat, srid))
                    lon, lat = rows[0]["lon"], rows[0]["lat"]
                ET.SubElement(segment, "trkpt", lat=str(lat), lon=str(lon))
        ET.indent(root)
        document = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"
        if gpx_file is not None:
            Path(gpx_file).write_text(document, encoding="utf-8")
        return document
