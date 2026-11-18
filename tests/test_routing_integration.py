from math import inf, nan
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from xml.etree import ElementTree as ET

import psycopg
from psycopg import sql

from psycopgr import PGRouting, PgrNode, __version__


DSN = os.environ.get("PSYCOPGR_TEST_DSN")
A = 3_000_000_001
B = 3_000_000_002
C = 3_000_000_003
D = 3_000_000_004


@unittest.skipUnless(DSN, "Set PSYCOPGR_TEST_DSN to a disposable routing database")
class RoutingIntegrationTest(unittest.TestCase):
    @staticmethod
    def check_database_messages(test, conn):
        messages = []

        def on_notice(diagnostic):
            if diagnostic.severity_nonlocalized == "WARNING":
                messages.append(diagnostic.message_primary)

        conn.add_notice_handler(on_notice)
        test(lambda: unittest.TestCase().assertEqual(messages, []))

    @classmethod
    def setUpClass(cls):
        cls.schema = f"routing_{uuid4().hex}"
        namespace = sql.Identifier(cls.schema)
        admin = psycopg.connect(DSN, autocommit=True)
        cls.addClassCleanup(admin.close)
        cls.check_database_messages(cls.addClassCleanup, admin)
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
            CREATE VIEW projected_vertices AS
                SELECT id, ST_Transform(the_geom, 3857) AS geom FROM ways_vertices_pgr;
            CREATE VIEW "Vertices With Space" AS
                SELECT id, the_geom AS "Vertex Point" FROM ways_vertices_pgr;
            CREATE VIEW "Edge Graph" AS
                SELECT gid AS "Edge ID", source, target, cost_s, reverse_cost_s FROM ways;
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
        self.check_database_messages(self.addCleanup, self.router._conn)
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

    def test_public_version_and_node_shape(self):
        self.assertEqual(__version__, "2.0.0")
        self.assertEqual(tuple(self.a), ("a", 0, 0))
        self.assertEqual(self.a._replace(lon=1), PgrNode("a", 1, 0))

    def test_connection_accepts_uri_and_legacy_database_keyword(self):
        params = psycopg.conninfo.conninfo_to_dict(DSN)
        params["database"] = params.pop("dbname")
        with PGRouting(**params, options=f"-c search_path={self.schema},public") as router:
            self.assertEqual(router.find_nearest_vertices([self.a]), [PgrNode(A, 0, 0)])
        self.assertTrue(router._conn.closed)
        router.close()

    def test_invalid_connection_propagates_psycopg_error(self):
        with self.assertRaises(psycopg.OperationalError):
            PGRouting(DSN, dbname="missing_routing_db_" + uuid4().hex)

    def test_sql_error_propagates_and_connection_remains_usable(self):
        self.router.set_meta_data(table="no_such_table")
        with self.assertRaises(psycopg.errors.UndefinedTable):
            self.router.dijkstra([A], [B])
        self.router.set_meta_data(table="ways")
        self.assertEqual(self.router.dijkstra_cost([A], [B]), {(A, B): 4})

    def test_metadata_validation_is_atomic_and_result_is_a_copy(self):
        for update in (
            {"missing_key": "ways"}, {"srid": 0}, {"srid": True}, {"srid": "4326"},
            {"directed": "false"}, {"has_reverse_cost": 1}, {"table": ""},
            {"table": ".ways"}, {"table": "db.schema.ways"}, {"id": ""},
            {"vertex_geometry": 42}, {"vertex_table": 4},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.router.set_meta_data(**update)
        with self.assertRaises(ValueError):
            self.router.set_meta_data(table="no_such_table", srid=0)
        returned = self.router.set_meta_data(vertex_table=None)
        returned["table"] = "no_such_table"
        self.assertEqual(self.router.get_costs(self.a, self.c), {(self.a, self.c): 9})

    def test_schema_qualified_quoted_identifiers(self):
        self.router.set_meta_data(
            table=f"{self.schema}.Edge Graph", id="Edge ID",
            vertex_table=f"{self.schema}.Vertices With Space", vertex_geometry="Vertex Point",
        )
        self.assertEqual(self.router.get_costs(self.a, self.c), {(self.a, self.c): 9})
        self.assertEqual(self.router.get_routes(self.a, self.c)[self.a, self.c]["path"][2].id, B)

    def test_nearest_vertices_preserve_order_and_duplicates(self):
        offset = PgrNode(None, 0.8, 0)
        self.assertEqual(self.router.find_nearest_vertices([self.c, offset, self.c]), [
            PgrNode(C, 2, 0), PgrNode(B, 1, 0), PgrNode(C, 2, 0),
        ])
        self.assertEqual(self.router.find_nearest_vertices([]), [])

    def test_distance_identical_symmetric_and_high_latitude(self):
        first = PgrNode(None, 0, 80)
        second = PgrNode(None, 1, 80)
        self.assertEqual(self.router.node_distance(first, first), 0)
        self.assertAlmostEqual(self.router.node_distance(first, second), 19393.25, places=2)
        self.assertAlmostEqual(self.router.node_distance(second, first), 19393.25, places=2)

    def test_projected_coordinates_and_gpx_are_transformed(self):
        self.router.set_meta_data(srid=3857, vertex_table="projected_vertices",
                                  vertex_geometry="geom")
        destination = PgrNode("projected", 222638.98158654713, 0)
        result = self.router.get_routes(self.a, destination)
        self.assertAlmostEqual(result[self.a, destination]["cost"], 9)
        self.assertAlmostEqual(self.router.node_distance(self.a, destination), 222638.98, places=2)
        document = ET.fromstring(self.router.get_gpx(result))
        points = document.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
        self.assertAlmostEqual(float(points[-1].attrib["lon"]), 2)
        self.assertAlmostEqual(float(points[-1].attrib["lat"]), 0)

    def test_multiple_endpoints_and_iterables(self):
        middle = PgrNode("b", 1, 0)
        expected = {(self.a, self.c): 9, (middle, self.a): 7, (middle, self.c): 5}
        self.assertEqual(self.router.get_costs(iter([self.a, middle]), (self.a, self.c)), expected)
        routes = self.router.get_routes((self.a, middle), iter([self.a, self.c]))
        self.assertEqual({pair: route["cost"] for pair, route in routes.items()}, expected)
        self.assertEqual(self.router.get_costs([self.a, middle], self.c), {
            (self.a, self.c): 9, (middle, self.c): 5,
        })

    def test_empty_identical_missing_and_unreachable_pairs(self):
        for route in (self.router.get_routes, self.router.get_costs):
            for start, end in (([], self.a), (self.a, []), (self.a, self.a), (self.c, self.a)):
                with self.subTest(start=start, end=end):
                    self.assertEqual(route(start, end), {})
        for low_level in (self.router.dijkstra, self.router.dijkstra_cost):
            self.assertEqual(low_level([], [A]), {})
            self.assertEqual(low_level([A], []), {})
            self.assertEqual(low_level([A], [100]), {})
        self.assertEqual(self.router.astar(C, A), {})
        self.assertEqual(self.router.astar(A, A), {})

    def test_undirected_routing_uses_the_forward_edges_in_reverse(self):
        self.assertEqual(self.router.dijkstra([C], [A]), {})
        self.router.set_meta_data(directed=False, has_reverse_cost=False)
        expected = {(C, A): 9}
        self.assertEqual(self.router.dijkstra_cost([C], [A]), expected)
        self.assertEqual(self.router.dijkstra([C], [A])[C, A]["cost"], 9)
        self.assertEqual(self.router.astar(C, A)[C, A]["cost"], 9)

    def test_endpoint_offsets_and_speed(self):
        near_start = PgrNode("near start", 0.001, 0)
        near_end = PgrNode("near end", 1.999, 0)
        key = (near_start, near_end)
        at_36 = self.router.get_costs(near_start, near_end, end_speed=36)[key]
        at_72 = self.router.get_costs(near_start, near_end, end_speed=72)[key]
        self.assertAlmostEqual(at_36, 9 + 2 * 111.31949 / 10, places=4)
        self.assertAlmostEqual(at_72, 9 + (at_36 - 9) / 2)
        self.assertEqual(self.router.get_routes(near_start, near_end, end_speed=36)[key]["cost"],
                         at_36)
        same_vertex = self.router.get_routes(self.a, near_start, end_speed=36)[self.a, near_start]
        self.assertEqual([node.id for node in same_vertex["path"]], ["a", A, "near start"])
        self.assertAlmostEqual(same_vertex["cost"], 111.31949 / 10, places=4)

    def test_invalid_speeds(self):
        for speed in (0, -1, inf, -inf, nan):
            for route in (self.router.get_routes, self.router.get_costs):
                with self.subTest(speed=speed), self.assertRaisesRegex(ValueError, "end_speed"):
                    route(self.a, self.c, end_speed=speed)

    def test_gpx_empty_and_multiple_tracks(self):
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        empty = ET.fromstring(self.router.get_gpx({}))
        self.assertEqual(empty.attrib, {"version": "1.1", "creator": "psycopgr"})
        self.assertEqual(list(empty), [])
        routes = self.router.get_routes(self.a, [PgrNode("b", 1, 0), self.c])
        doc = ET.fromstring(self.router.get_gpx(routes))
        self.assertEqual(len(doc.findall("g:trk", ns)), 2)
        self.assertEqual([len(track.findall("g:trkseg/g:trkpt", ns))
                          for track in doc.findall("g:trk", ns)], [4, 5])
        self.assertEqual(doc.find("g:trk/g:name", ns).text, "0,0->1,0: 4.0")
