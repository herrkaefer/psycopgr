        ____  _______  ___________  ____  ____ ______
       / __ \/ ___/ / / / ___/ __ \/ __ \/ __ `/ ___/
      / /_/ (__  ) /_/ / /__/ /_/ / /_/ / /_/ / /
     / .___/____/\__, /\___/\____/ .___/\__, /_/
    /_/         /____/          /_/    /____/

[![PyPI](https://img.shields.io/pypi/v/psycopgr.svg)](https://pypi.org/project/psycopgr/)
[![PyPI - License](https://img.shields.io/pypi/l/psycopgr.svg)](https://pypi.org/project/psycopgr/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/psycopgr.svg)

`psycopgr` is a Python wrapper of [pgRouting](http://pgrouting.org/) with one purpose:

**Computing routes on real map for humans.**

Tested with

- Python 3.6.5
- PostgreSQL 11.2
- PostGIS 2.5.2
- pgRouting 2.6.2
- osm2pgrouting 2.3.6

## Preparation 

- Install `PostgreSQL`, `PostGIS`, and `pgRouting`
- Create database to store map data
- Import OpenStreet map data into database

A step by step note can be found [here](https://herrkaefer.com/2016/08/30/pgrouting-notes/).

## Installation

```sh
pip install psycopgr
```

or

```sh
pipenv install psycopgr
```

## Routing with Python!

First,

```python
from psycopgr import PgrNode, PGRouting
```

Create an PGRouting instance with database connection:

```python
pgr = PGRouting(database='mydb', user='user')
```

Adjust meta datas of tables including the edge table properies if they are different from the default (only the different properties needs to be set), e.g.:

```python
pgr.set_meta_data(cost='cost_s', reverse_cost='reverse_cost_s', directed=true)
```

This is the default meta data:

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

Nodes are points on map which are represented by `PgrNode` namedtuple with geographic coordinates (longitude and latitude) rather than vague vertex id (vid) in the tables. `PgrNodes` is defined as:

```python
PgrNode = namedtuple('PgrNode', ['id', 'lon', 'lat'])
```

in which `id` could be `None` or self-defined value, and `lon` and `lat` are double precision values. 

For example:

```python
nodes = [PgrNode(None, 116.30150, 40.05500),
         PgrNode(None, 116.36577, 40.00253),
         PgrNode(None, 116.30560, 39.95458),
         PgrNode(None, 116.46806, 39.99857)]
```

Now we can do routings! This is really straightforward:

```python
# many-to-many
routings = pgr.get_routes(nodes, nodes, end_speed=5.0, pgx_file='r.pgx')
# one-to-one
routings = pgr.get_routes(nodes[0], nodes[1])
# one-to-many
routings = pgr.get_routes(nodes[0], nodes)
# many-to-one
routings = pgr.get_routes(nodes, node[2])
```

- `end_speed`: speed from node to nearest vertices on ways in unit km/h.
- `gpx_file`: set it to output paths to a gpx file.

The returned is a dict of dict: `{(start_node, end_node): {'path': [PgrNode], 'cost': cost}`

By default, `cost` is traveling time along the path in unit second. It depends on the means of columns of the edge table that you set as `cost` and `reverse_cost`. You can assign the relations by `set_meta_data` function.

We can also get only costs without detailed paths returned:

```python
costs = pgr.get_costs(nodes, nodes)
```

The returned is also a dict: `{(start_node, end_node): cost}`

## Low-level wrapper of pgRouting functions

| psycopgr function | pgRouting function |
| :---------------- | :----------------- |
| dijkstra          | pgr_dijkstra       |
| dijkstra_cost     | pgr_dijkstraCost   |
| astar             | pgr_astar          |

These are direct wrappings of pgRouting functions. For example, `dijkstra` takes vertex ids as input. This list may be extended in the future.

## Tutorial

Here is a [tutorial](https://herrkaefer.com/2016/09/01/psycopgr-tutorial/).

