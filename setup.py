from distutils.core import setup

requires = [
    'psycopg2',
]

setup(
    name='psycopgr',
    version='1.0.1',
    description = 'A Python wrapper of pgRouting for routing from nodes to nodes on real map.',
    url='https://github.com/herrkaefer/psycopgr',
    download_url = 'https://github.com/herrkaefer/psycopgr/archive/v1.0.1.tar.gz',
    author='Yang Liu',
    author_email='gloolar@gmail.com',
    license='MIT',
    keywords='routing GIS PostgreSQL pgRouting PostGIS shortest path A* Dijkstra map',
    packages=['psycopgr'],
    install_requires=requires
)
