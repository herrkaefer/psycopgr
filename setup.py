from setuptools import setup

requires = [
    'psycopg2',
]

setup(
    name='psycopgr',
    version='1.0.1',
    url='https://github.com/herrkaefer/psycopgr',
    author='Yang Liu',
    author_email='gloolar@gmail.com',
    license='MIT',
    keywords='routing GIS PostgreSQL pgRouting PostGIS shortest path A* Dijkstra',
    packages=['psycopgr'],
    install_requires=requires
)
