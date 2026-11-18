import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from xml.etree import ElementTree as ET

import psycopg
from psycopg import sql

from psycopgr import PGRouting, PgrNode


DSN = os.environ.get("PSYCOPGR_TEST_DSN")
A = 3_000_000_001
B = 3_000_000_002
C = 3_000_000_003
D = 3_000_000_004


@unittest.skipUnless(DSN, "Set PSYCOPGR_TEST_DSN to a disposable routing database")
class RoutingIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"routing_{uuid4().hex}"
        namespace = sql.Identifier(cls.schema)
        admin = psycopg.connect(DSN, autocommit=True)
        cls.addClassCleanup(admin.close)
        admin.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        admin.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
        versions = admin.execute(
            "SELECT current_setting('server_version'), postgis_lib_version(), pgr_version()"
        ).fetchone()
        print("PostgreSQL %s, PostGIS %s, pgRouting %s" % versions)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(namespace))
        cls.addClassCleanup(admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(namespace))
        admin.execute(sql.SQL("SET search_path TO {}, public").format(namespace))
        admin.execute("""
            CREATE TABLE ways_vertices_pgr (
                id bigint PRIMARY KEY,
                the_geom geometry(Point, 4326)
            );
            CREATE INDEX ON ways_vertices_pgr USING gist(the_geom);
            CREATE TABLE ways (
                gid bigint PRIMARY KEY, source bigint, target bigint,
                cost_s float8, reverse_cost_s float8,
                x1 float8, y1 float8, x2 float8, y2 float8
            );
            INSERT INTO ways_vertices_pgr
            SELECT id, ST_SetSRID(ST_MakePoint(lon, 0), 4326)
            FROM (VALUES (3000000001, 0), (3000000002, 1),
                         (3000000003, 2), (3000000004, 3)) AS v(id, lon);
            INSERT INTO ways VALUES
                (3000000011, 3000000001, 3000000002, 4, 7, 0, 0, 1, 0),
                (3000000012, 3000000002, 3000000003, 5, -1, 1, 0, 2, 0),
                (3000000013, 3000000001, 3000000003, 12, -1, 0, 0, 2, 0);
            CREATE VIEW modern_ways_vertices_pgr AS
                SELECT id, the_geom AS geom FROM ways_vertices_pgr;
            CREATE VIEW modern_ways AS
                SELECT gid AS id, source, target, cost_s, reverse_cost_s, x1, y1, x2, y2
                FROM ways;
            CREATE VIEW forward_only AS
                SELECT gid, source, target, cost_s FROM ways;
        """)

    def setUp(self):
        self.router = PGRouting(DSN, options=f"-c search_path={self.schema},public")
        self.addCleanup(self.router.close)
        self.a = PgrNode("a", 0, 0)
        self.c = PgrNode("c", 2, 0)
        self.d = PgrNode("d", 3, 0)

    def test_bigint_paths_and_reverse_cost(self):
        for routes in (self.router.dijkstra([A], [C]), self.router.astar(A, C)):
            self.assertEqual([point.id for point in routes[A, C]["path"]], [A, B, C])
            self.assertEqual(routes[A, C]["cost"], 9)
        self.assertEqual(self.router.dijkstra_cost([B, C], [A]), {(B, A): 7})

    def test_coordinates_costs_gpx_and_unreachable(self):
        self.assertAlmostEqual(self.router.node_distance(self.a, PgrNode(None, 1, 0)),
                               111319.49, places=2)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "route.gpx"
            routes = self.router.get_routes(self.a, [self.c, self.d], gpx_file=output)
            self.assertEqual(list(routes), [(self.a, self.c)])
            self.assertEqual(routes[self.a, self.c]["cost"], 9)
            points = ET.parse(output).findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
            self.assertEqual([float(point.attrib["lon"]) for point in points], [0, 0, 1, 2, 2])
        self.assertEqual(self.router.get_costs(self.a, [self.c, self.d]), {(self.a, self.c): 9})
        self.assertEqual(self.router.get_routes(self.d, self.a), {})
        near = PgrNode("near", 0.00001, 0)
        self.assertGreater(self.router.get_costs(self.a, near)[self.a, near], 0)
        with self.assertRaises(ValueError):
            self.router.get_routes(self.a, self.c, end_speed=0)

    def test_modern_schema_and_metadata_isolation(self):
        self.router.set_meta_data(table=f"{self.schema}.modern_ways", id="id",
                                  geometry="geom", vertex_geometry="geom")
        self.assertEqual(self.router.get_costs(self.a, self.c), {(self.a, self.c): 9})
        with PGRouting(DSN, options=f"-c search_path={self.schema},public") as legacy:
            self.assertEqual(legacy.get_costs(self.a, self.c), {(self.a, self.c): 9})

    def test_reverse_cost_column_can_be_omitted(self):
        self.router.set_meta_data(table="forward_only", vertex_table="ways_vertices_pgr",
                                  has_reverse_cost=False)
        self.assertEqual(self.router.dijkstra_cost([A, C], [A, C]), {(A, C): 9})
        self.assertEqual(self.router.get_routes(self.a, self.c)[self.a, self.c]["cost"], 9)

    def test_empty_vertices(self):
        self.router.set_meta_data(vertex_table="empty_vertices")
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("""
                CREATE TABLE {}.empty_vertices (id bigint, the_geom geometry(Point, 4326))
            """).format(sql.Identifier(self.schema)))
        self.assertEqual(self.router.find_nearest_vertices([self.a]), [None])
        self.assertEqual(self.router.get_routes(self.a, self.c), {})
