import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

import psycopg
from psycopg.conninfo import conninfo_to_dict

from psycopgr import PGRouting, PgrNode


DSN = os.environ.get("PSYCOPGR_TEST_DSN")
IMPORTER = os.environ.get("OSM2PGROUTING")
MAPCONFIG = os.environ.get("OSM_MAPCONFIG")

# Two residential OSM ways meet at the middle node.
OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="psycopgr-tests">
  <node id="1" lat="41.880" lon="-87.630"/>
  <node id="2" lat="41.880" lon="-87.629"/>
  <node id="3" lat="41.881" lon="-87.629"/>
  <way id="101">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/>
  </way>
  <way id="102">
    <nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/>
  </way>
</osm>
"""


@unittest.skipUnless(DSN and IMPORTER and MAPCONFIG, "Requires osm2pgrouting and a test database")
class OsmImportTest(unittest.TestCase):
    def test_osm_3_import_and_bidirectional_routing(self):
        options = conninfo_to_dict(DSN)
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
            conn.execute("CREATE SCHEMA osm_fixture")
            self.addCleanup(self.drop_fixture)
        with TemporaryDirectory() as directory:
            fixture = Path(directory) / "network.osm"
            fixture.write_text(OSM, encoding="utf-8")
            result = subprocess.run([
                IMPORTER, "--file", str(fixture), "--conf", MAPCONFIG,
                "--dbname", options["dbname"], "--host", options["host"],
                "--port", options["port"], "--username", options["user"],
                "--password", options["password"], "--schema", "osm_fixture",
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotRegex(result.stdout + result.stderr, r"(?im)^\s*(warning|error):")

        start = PgrNode("start", -87.630, 41.880)
        middle = PgrNode("middle", -87.629, 41.880)
        end = PgrNode("end", -87.629, 41.881)
        with PGRouting(DSN) as router:
            router.set_meta_data(table="osm_fixture.ways", id="id", vertex_geometry="geom")
            by_way = router.get_routes([start, end], [start, end])
            self.assertEqual(set(by_way), {(start, end), (end, start)})
            for (source, destination), route in by_way.items():
                self.assertEqual((route["path"][0], route["path"][-1]), (source, destination))
                self.assertEqual([(node.lon, node.lat) for node in route["path"][1:-1]],
                                 [(node.lon, node.lat) for node in (source, middle, destination)])
                self.assertGreater(route["cost"], 0)
            self.assertEqual(router.get_costs([start, end], [start, end]),
                             {key: route["cost"] for key, route in by_way.items()})

    def drop_fixture(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA osm_fixture CASCADE")
