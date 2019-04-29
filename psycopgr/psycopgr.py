#!/usr/bin/env python
#
# Yang Liu (gloolar@gmail.com)
# 2016-08
#
# TODO
# - Node range check
# - Thinking about PgrNode as key: format node lat, lon precision?

import psycopg2
import psycopg2.extras
from collections import namedtuple


__all__ = ['PgrNode', 'PGRouting']


PgrNode = namedtuple('PgrNode', ['id', 'lon', 'lat'])


class PGRouting(object):
    """Computing shortest paths and costs from nodes to nodes represented in
    geographic coordinates, by wrapping pgRouting.
    """

    __conn = None
    __cur = None

    # default edge table defination
    __meta_data = {
        'table': 'ways',
        'id': 'gid',
        'source': 'source',
        'target': 'target',
        'cost': 'cost_s',  # driving time in second
        'reverse_cost': 'reverse_cost_s',  # reverse driving time in second
        'x1': 'x1',
        'y1': 'y1',
        'x2': 'x2',
        'y2': 'y2',
        'geometry': 'the_geom',
        'has_reverse_cost': True,
        'directed': True,
        'srid': 4326
    }

    def __init__(self, database, user, host='localhost', port='5432'):
        self.__connect_to_db(database, user, host, port)

    def __del__(self):
        self.__close_db()

    def __connect_to_db(self, database, user, host, port):
        if self.__cur is not None and not self.__cur.closed:
            self.__cur.close()
        if self.__conn is not None and not self.__conn.closed:
            self.__conn.close()

        try:
            self.__conn = psycopg2.connect(database=database, user=user,
                                           host=host, port=port)
            self.__cur = self.__conn.cursor(
                cursor_factory=psycopg2.extras.DictCursor)
        except psycopg2.Error as e:
            print(e.pgerror)

    def __close_db(self):
        if not self.__cur.closed:
            self.__cur.close()
        if not self.__conn.closed:
            self.__conn.close()

    def __find_nearest_vertices(self, nodes):
        """Find nearest vertex of nodes on the way.

        Args:
            nodes: list of PgrNode.

        Returns:
            list of PgrNode.
        """

        sql = """
            SELECT id, lon::double precision, lat::double precision
            FROM {table}_vertices_pgr
            ORDER BY the_geom <-> ST_SetSRID(ST_Point(%s,%s),{srid})
            LIMIT 1
            """.format(table=self.__meta_data['table'],
                       srid=self.__meta_data['srid'])

        output = []
        for node in nodes:
            try:
                self.__cur.execute(sql, (node.lon, node.lat))
                results = self.__cur.fetchall()
                if len(results) > 0:
                    output.append(PgrNode(results[0]['id'],
                                  results[0]['lon'],
                                  results[0]['lat']))
                else:
                    print('cannot find nearest vid for ({}, {})'.format(
                          node[0], node[1]))
                    return None
            except psycopg2.Error as e:
                print(e.pgerror)
                return None
        return output

    def __node_distance(self, node1, node2):
        """Get distance between two nodes (unit: m).
        """
        sql = """
            SELECT ST_Distance(
                ST_GeogFromText('SRID={srid};POINT({lon1} {lat1})'),
                ST_GeogFromText('SRID={srid};POINT({lon2} {lat2})')
            );
            """.format(srid=self.__meta_data['srid'],
                       lon1=node1.lon, lat1=node1.lat,
                       lon2=node2.lon, lat2=node2.lat)

        try:
            self.__cur.execute(sql)
            results = self.__cur.fetchall()
            return results[0][0]

        except psycopg2.Error as e:
            print(e.pgerror)
            return None

    def set_meta_data(self, **kwargs):
        """Set meta data of tables if it is different from the default.
        """
        for k, v in kwargs.items():
            if k not in self.__meta_data.keys():
                print("WARNNING: set_meta_data: invaid key {}".format(k))
                continue
            if not isinstance(v, (str, bool, int)):
                print("WARNNING: set_meta_data: invalid value {}".format(v))
                continue
            self.__meta_data[k] = v
        return self.__meta_data

    def dijkstra_cost(self, start_vids, end_vids):
        """Get all-pairs costs among way nodes without paths using
        pgr_dijkstraCost function.
        """

        sql = """
            SELECT *
            FROM pgr_dijkstraCost(
                'SELECT {id} as id,
                        {source} as source,
                        {target} as target,
                        {cost} as cost,
                        {reverse_cost} as reverse_cost
                 FROM {table}',
                %s,
                %s,
                {directed})
            """.format(
                    table=self.__meta_data['table'],
                    id=self.__meta_data['id'],
                    source=self.__meta_data['source'],
                    target=self.__meta_data['target'],
                    cost=self.__meta_data['cost'],
                    reverse_cost=self.__meta_data['reverse_cost'],
                    directed='TRUE'
                             if self.__meta_data['directed']
                             else 'FALSE')

        try:
            self.__cur.execute(sql, (start_vids, end_vids))
            results = self.__cur.fetchall()
            return {(r['start_vid'], r['end_vid']): r['agg_cost']
                    for r in results}

        except psycopg2.Error as e:
            print(e.pgerror)
            return {}

    def dijkstra(self, start_vids, end_vids):
        """Get all-pairs shortest paths with costs among way nodes using
        pgr_dijkstra function.
        """

        sql = """
            SELECT *, v.lon::double precision, v.lat::double precision
            FROM
                pgr_dijkstra(
                    'SELECT {id} as id,
                            {source} as source,
                            {target} as target,
                            {cost} as cost,
                            {reverse_cost} as reverse_cost
                     FROM {edge_table}',
                    %s,
                    %s,
                    {directed}) as r,
                {edge_table}_vertices_pgr as v
            WHERE r.node=v.id
            ORDER BY r.seq;
            """.format(
                    edge_table=self.__meta_data['table'],
                    id=self.__meta_data['id'],
                    source=self.__meta_data['source'],
                    target=self.__meta_data['target'],
                    cost=self.__meta_data['cost'],
                    reverse_cost=self.__meta_data['reverse_cost'],
                    directed='TRUE'
                             if self.__meta_data['directed']
                             else 'FALSE')

        try:
            self.__cur.execute(sql, (start_vids, end_vids))
            results = self.__cur.fetchall()

            output = {}
            for r in results:
                # print r
                key = (r['start_vid'], r['end_vid'])
                if output.get(key, None) is None:
                    output[key] = {'path': [], 'cost': -1}

                output[key]['path'].append(
                    PgrNode(r['node'], r['lon'], r['lat']))
                if r['edge'] < 0:
                    output[key]['cost'] = r['agg_cost']

            return output

        except psycopg2.Error as e:
            print(e.pgerror)
            return {}

    def astar(self, start_vid, end_vid):
        """Get one-to-one shortest path between way nodes using pgr_AStar
        function.
        """

        sql = """
            SELECT *, v.lon::double precision, v.lat::double precision
            FROM
                pgr_AStar(
                    'SELECT {id}::INTEGER as id,
                            {source}::INTEGER as source,
                            {target}::INTEGER as target,
                            {cost} as cost,
                            {x1} as x1,
                            {y1} as y1,
                            {x2} as x2,
                            {y2} as y2
                            {reverse_cost}
                     FROM {edge_table}',
                    %s,
                    %s,
                    {directed},
                    {has_rcost}) as r,
                {edge_table}_vertices_pgr as v
            WHERE r.id1=v.id
            ORDER BY r.seq;
            """.format(
                    edge_table=self.__meta_data['table'],
                    id=self.__meta_data['id'],
                    source=self.__meta_data['source'],
                    target=self.__meta_data['target'],
                    cost=self.__meta_data['cost'],
                    x1=self.__meta_data['x1'],
                    y1=self.__meta_data['y1'],
                    x2=self.__meta_data['x2'],
                    y2=self.__meta_data['y2'],
                    reverse_cost=', {} as reverse_cost'
                    .format(self.__meta_data['reverse_cost'])
                    if (self.__meta_data['directed']
                        and self.__meta_data['has_reverse_cost'])
                    else '',
                    directed='TRUE'
                             if self.__meta_data['directed']
                             else 'FALSE',
                    has_rcost='TRUE'
                              if (self.__meta_data['directed']
                                  and self.__meta_data['has_reverse_cost'])
                              else 'FALSE'
                )
        # print(sql)

        try:
            self.__cur.execute(sql, (start_vid, end_vid))
            results = self.__cur.fetchall()

            output = {}
            key = (start_vid, end_vid)
            for r in results:
                # print r
                if output.get(key, None) is None:
                    output[key] = {'path': [], 'cost': 0}

                output[key]['path'].append(
                    PgrNode(r['id1'], r['lon'], r['lat']))
                if r['id2'] > 0:
                    output[key]['cost'] += r['cost']

            return output

        except psycopg2.Error as e:
            print(e.pgerror)
            return {}

    def __get_one_to_one_routing(self, start_node, end_node, end_speed=10.0):
        """Get one-to-one shorest path using A* algorithm.

        Args:
            start_node and end_node: PgrNode.
            end_speed: speed from node to nearest vertex on way (unit: km/h)

        Returns:
            Routing dict with key (start_node, end_node), and path and cost
            in values. Cost is travelling time in second.
        """

        if start_node == end_node:
            return {}

        end_speed = end_speed * 1000.0 / 3600.0  # km/h -> m/s
        vertices = self.__find_nearest_vertices([start_node, end_node])
        node_vertex_costs = [
            self.__node_distance(start_node, vertices[0])/end_speed,
            self.__node_distance(end_node, vertices[1])/end_speed
        ]

        # routing between vertices
        main_routing = self.astar(vertices[0].id, vertices[1].id)

        routing = {
            (start_node, end_node): {
                'cost':
                    main_routing[(vertices[0].id, vertices[1].id)]['cost']
                    + node_vertex_costs[0]
                    + node_vertex_costs[1],
                'path':
                    [start_node]
                    + main_routing[(vertices[0].id, vertices[1].id)]['path']
                    + [end_node]
            }
        }

        return routing

    def __get_all_pairs_routings(self, start_nodes, end_nodes=None,
                                 end_speed=10.0):
        """Get all-pairs shortest paths from start_nodes to end_nodes with costs
        using Dijkstra algorithm.

        Args:
            start_nodes and end_nodes: lists of PgrNode.
            end_speed: speed from node to nearest vertex on way (unit: km/h)

        Returns:
            A dict with key (start_node, end_node), and path and cost in
            values. Cost is travelling time with unit second.
        """

        end_speed = end_speed * 1000.0 / 3600.0  # km/h -> m/s

        if end_nodes is not None:
            node_set = set(start_nodes) | set(end_nodes)
        else:
            node_set = set(start_nodes)
            end_nodes = start_nodes

        node_list = list(node_set)

        vertices = self.__find_nearest_vertices(node_list)
        node_vertex = {
            node: {
                'vertex': vertex,
                'cost': self.__node_distance(node, vertex) / end_speed
            }
            for node, vertex in zip(node_list, vertices)
        }

        start_vids = [node_vertex[node]['vertex'].id for node in start_nodes]
        end_vids = [node_vertex[node]['vertex'].id for node in end_nodes]

        # routings from vertices to vertices on ways
        main_routings = self.dijkstra(start_vids, end_vids)

        routings = {
            (start_node, end_node): {
                'cost':
                    main_routings[(node_vertex[start_node]['vertex'].id,
                                  node_vertex[end_node]['vertex'].id)]['cost']
                    + node_vertex[start_node]['cost']
                    + node_vertex[end_node]['cost'],
                'path':
                    [start_node]
                    + main_routings[
                        (node_vertex[start_node]['vertex'].id,
                         node_vertex[end_node]['vertex'].id)
                      ]['path']
                    + [end_node]
                }
            for start_node in start_nodes
            for end_node in end_nodes
            if start_node != end_node
        }

        return routings

    def __get_all_pairs_costs(self, start_nodes, end_nodes=None,
                              end_speed=10.0):
        """Get all-pairs shortest paths' costs without path details.

        Args:
            start_nodes and end_nodes: lists of PgrNode. end_nodes is None
                means it is the same as start_nodes.
            end_speed: speed from node to nearest vertex on way (unit: km/h).

        Returns:
            A dict with key (start_node, end_node), and values cost. Cost is
            travelling time in second.
        """

        end_speed = end_speed*1000.0/3600.0  # km/h -> m/s

        if end_nodes is not None:
            node_set = set(start_nodes) | set(end_nodes)
        else:
            node_set = set(start_nodes)
            end_nodes = start_nodes

        node_list = list(node_set)
        vertices = self.__find_nearest_vertices(node_list)
        node_vertex = {
            node: {'vertex': vertex,
                   'cost': self.__node_distance(node, vertex) / end_speed}
            for node, vertex in zip(node_list, vertices)
        }

        start_vids = [node_vertex[node]['vertex'].id for node in start_nodes]
        end_vids = [node_vertex[node]['vertex'].id for node in end_nodes]

        # routings' costs from vertices to vertices on ways
        main_costs = self.dijkstra_cost(start_vids, end_vids)

        # total costs = main cost + two ends costs
        costs = {
            (start_node, end_node):
            main_costs[(node_vertex[start_node]['vertex'].id,
                       node_vertex[end_node]['vertex'].id)]
            + node_vertex[start_node]['cost']
            + node_vertex[end_node]['cost']
            for start_node in start_nodes
            for end_node in end_nodes
            if start_node != end_node
        }

        return costs

    def get_routes(self, start_nodes, end_nodes, end_speed=10.0,
                   gpx_file=None):
        """Get shortest paths from nodes to nodes.

        Args:
            start_nodes: PgrNode list for many nodes, or PgrNode for one node.
            end_nodes: PgrNode list for many nodes, or PgrNode for one node.
            end_speed: speed for travelling from end node to corresponding
                nearest node on the way.
            gpx_file: name of file for saving the paths as gpx format.

        Returns:
            A dict mapping node pair (start_node, end_node) to dict of
            corresponding path and cost. Path is a Pgrnode list, and cost is
            travelling time in second.
        """

        if not isinstance(start_nodes, list):
            start_nodes = [start_nodes]
        if not isinstance(end_nodes, list):
            end_nodes = [end_nodes]

        routes = {}

        # many-to-one or one-to-one
        if len(end_nodes) == 1:
            for start_node in start_nodes:
                r = self.__get_one_to_one_routing(start_node,
                                                  end_nodes[0],
                                                  end_speed)
                routes.update(r)

        # one-to-many or many-to-many
        else:
            routes = self.__get_all_pairs_routings(start_nodes,
                                                   end_nodes,
                                                   end_speed)

        if gpx_file is not None:
            self.get_gpx(routes, gpx_file)

        return routes

    def get_costs(self, start_nodes, end_nodes, end_speed=10.0):
        """Get costs from nodes to nodes without paths.

        Args:
            start_nodes: PgrNode list for many nodes, or PgrNode for one node.
            end_nodes: PgrNode list for many nodes, or PgrNode for one node.
            end_speed: speed for travelling from end node to corresponding
                nearest node on the way.

        Returns:
            A dict mapping all node pairs (start_node, end_node) to
            corresponding costs. Cost is travelling time in second.
        """

        if not isinstance(start_nodes, list):
            start_nodes = [start_nodes]
        if not isinstance(end_nodes, list):
            end_nodes = [end_nodes]

        output = {}
        # many-to-one or one-to-one
        if len(end_nodes) == 1:
            for start_node in start_nodes:
                routing = self.__get_one_to_one_routing(
                    start_node, end_nodes[0], end_speed)
                for k, v in routing.items():
                    output.update({k: v['cost']})
            return output

        return self.__get_all_pairs_costs(start_nodes, end_nodes, end_speed)

    def get_gpx(self, routes, gpx_file=None):
        """Get gpx representation of routes.

        Args:
            routes: routes returned by get_routes.
            gpx_file: name of file for saving gpx data.

        Returns:
            gpx string of paths in routes. Saved in gpx_file if it is
            specified.
        """

        output = ''
        output += "<?xml version='1.0'?>\n"
        output += ("<gpx version='1.1' creator='psycopgr' "
                   "xmlns='http://www.topografix.com/GPX/1/1' "
                   "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
                   "xsi:schemaLocation='http://www.topografix.com/GPX/1/1 "
                   "http://www.topografix.com/GPX/1/1/gpx.xsd'>\n")

        for key, value in routes.items():
            output = output + " <trk>\n"
            output = output + "  <name>{},{}->{},{}: {}</name>\n".format(
                              key[0].lon,
                              key[0].lat,
                              key[1].lon,
                              key[1].lat,
                              value.get('cost', None))
            output = output + "  <trkseg>\n"

            for node in value['path']:
                # print(node)
                output = output + "   <trkpt lat='{}' lon='{}'>\n".format(
                                  node.lat,
                                  node.lon)
                output = output + "   </trkpt>\n"
            output = output + "  </trkseg>\n  </trk>\n"

        output = output + "</gpx>\n"

        if gpx_file is not None:
            with open(gpx_file, "w") as f:
                f.write(output)
            print("gpx saved to {}".format(gpx_file))

        return output


def test1():
    pgr = PGRouting(database='pgroutingtest', user='herrk')
    pgr.set_meta_data(table='edge_table', id='id', cost='cost')

    costs = pgr.dijkstra_cost([2, 11], [3, 5])
    print("\nall-pairs costs:\n")
    print(costs)

    routings = pgr.dijkstra([2, 11], [3, 5])
    print("\nall-pairs paths with costs:\n")
    print(routings)

    routing = pgr.astar(11, 3)
    print("\none-to-one path:\n")
    print(routing)


def test2():
    pgr = PGRouting(database='mydb', user='herrk')

    routing = pgr.astar(100, 111)
    print("\nrouting:\n")
    print(routing)

    routings = pgr.dijkstra([100, 400], [200, 600])
    print("\nroutings:\n")
    print(routings)

    gpx = pgr.get_gpx(routings, 'b.gpx')
    print(gpx)


def test3():
    pgr = PGRouting(database='mydb', user='herrk')
    nodes = [PgrNode(None, 116.30150, 40.05500),
             PgrNode(None, 116.36577, 40.00253),
             PgrNode(None, 116.30560, 39.95458),
             PgrNode(None, 116.46806, 39.99857)]

    routings = pgr.get_routes(nodes, nodes)
    # pprint(routings)

    costs = pgr.get_costs(nodes, nodes)
    # pprint(costs)

    keys = [(s, t) for s in nodes for t in nodes if s != t]
    for s, t in keys:
        r = pgr.get_routes(s, t)
        c = pgr.get_costs(s, t)
        print("\ncompare")
        print(routings[(s, t)]['cost'])
        print(costs[(s, t)])
        print(r[(s, t)]['cost'])
        print(c[(s, t)])

    s = nodes[0]
    t = nodes[3]
    r = pgr.get_routes(s, t, gpx_file='test/r-astar.gpx')
    c = pgr.get_costs(s, t)
    print(r[(s, t)]['cost'])
    print(c[(s, t)])

    pgr.get_gpx({(s, t): routings[(s, t)]}, gpx_file='test/r-dijkstra.gpx')


def main():
    test3()


if __name__ == '__main__':
    main()
