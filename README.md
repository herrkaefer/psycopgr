`psycopgr`: a pgRouting Python wrapper.


## What psycopgr is for

As said in [pgRouting docs](http://workshop.pgrouting.org/2.1.0-dev/en/chapters/wrapper.html):

>
Just considering the different ways that the cost can be calculated, makes it almost impossible to create a general wrapper, that can work on all applications.

Actually, in many applicatons you may need to modify the database tables and fill some computed values to fit your specific purpose, which is often done in a preprocessing stage by SQL, before real routing works. It is not appropriate to be wrapped.

So after preprocessing things such as database creation, map data import, talbes re-calculation and update, you are ready to use `psycopgr` to do another simple thing: **computing routes from nodes to nodes on real map.** 

Note that `psycopgr` is never a general purpose wrapper of pgRouting. I am a novice in GIS and what I want from psycopgr is just routes (or costs) from places to places. For preprocessing stage, I have a post ["pgRouting for dummies"](http://herrkaefer.online/2016/08/30/pgrouting-for-dummies/) for my own reference.

## Tutorial

Create a PGRouting object with database connection: (as you may guessed, `psycopgr` uses `psycopg2` as PostgreSQL driver)

```python
pgr = PGRouting(database='mydb', user='user')
```

Set the edge table properies if they are different from the default, e.g.

```python
pgr.set_edge_table(cost='cost_s', reverse_cost='reverse_cost_s', directed=true)
```

The default edge table is:

```python
{
    'table': 'ways',
    'id': 'gid',
    'source': 'source',
    'target': 'target',
    'cost': 'cost_s', # driving time in second
    'reverse_cost': 'reverse_cost_s', # reverse driving time in second
    'x1': 'x1',
    'y1': 'y1',
    'x2': 'x2',
    'y2': 'y2',
    'geometry': 'the_geom',
    'has_reverse_cost': True,
    'directed': True,
    'srid': 4326
}
```

Prepare nodes. nodes are represented by `PgrNode` namedtuple, in which `id` could be `None` or self-defined value, and `lon` and `lat` are double precision values. Of course nodes could be input from various interfaces such as databese or another program.

An example:

```python
nodes = [PgrNode(None, 116.30150, 40.05500),
         PgrNode(None, 116.36577, 40.00253),
         PgrNode(None, 116.30560, 39.95458),
         PgrNode(None, 116.46806, 39.99857)]
```

Routing from nodes to nodes:

```python
# many-to-many
routings = pgr.get_routes(nodes, nodes, end_speed=5.0, pgx_file='r.pgx')
# one-to-one
routings = pgr.get_routes(nodes[0], node[2])
# one-to-many
routings = pgr.get_routes(nodes[0], nodes)
# many-to-one
routings = pgr.get_routes(nodes, node[2])
```

- `end_speed`: speed from node to nearest vertices on ways in unit km/h.
- `gpx_file`: set it to output paths to a gpx file.

The returned is a dict: {`(start_node, end_node): {'path': [PgrNode], 'cost': cost}`

By default, `cost` is traveling time along the path in unit second. It depends what column in edge table and its contents you set as `cost` and `reverse_cost`. You can change this using `set_edge_table`.

We can also get only costs without detailed paths returned:

```python
costs = pgr.get_costs(nodes, nodes)
```

## Low-level wrapper of pgRouting functions

- `dijkstra`
- `dijkstra_cost`
- `astar`

These are direct wrappings of pgRouting functions. For example, `dijkstra` takes vertex ids as input. This list may be extended in the future.


