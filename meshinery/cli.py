#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# External imports
import networkx as nx

from docopt import docopt
from pprint import pformat
from pygraphviz import AGraph
from pyroute2 import IPDB, netns, NetNS

# Standard library imports
import argparse
import logging
import os
import sys

USAGE = """
Meshinery - the mesh network testing toolkit.

Usage:
    meshinery DOT_GRAPH_FILE [--id ID] [--dry-run --verbose --strays]
    meshinery clean DOT_GRAPH_FILE --id ID [--dry-run --verbose --strays]
    meshinery -h | --help

Options:
    --clean         Remove the namespaces associated with DOT_GRAPH_FILE
    --dry-run       Dry run mode.
    --id ID         Set a name for the Meshinery instance to use
    --strays        When cleaning up, attempt to remove all interfaces from the global namespace
    --verbose       Log at debug level.
    -h --help       Show this screen.
    DOT_GRAPH_FILE  A file containing the description of the mesh to emulate.
"""

def clean(graph, dry_run=False, instance_id=None, strays=False):
    """
    Remove namespaces listed in :data:`graph` and stop scripts

    :param networkx.Graph graph: The graph for which we want to clean up
    :param bool dry_run: If set makes Meshinery not touch any namespaces
    :param str instance_id: If set changes the middle section of each
    namespace's name; current PID by default
    """
    if instance_id is None:
        instance_id = os.getpid()

    # Remove namespaces
    for node_name in graph:
        ns_name = "meshinery-{}-{}".format(instance_id, node_name)
        try:
            if not dry_run:
                netns.remove(ns_name)
                logging.info('Removed namespace {}'.format(ns_name))
        except FileNotFoundError as e:
            logging.debug('Namespace %s doesn\'t exist - not removing' % ns_name)

    # Remove stray interfaces
    if strays:
        ipdb = IPDB()
        for node_name_a, node_name_b in graph.edges:
            if_name = "{}-{}".format(node_name_a, node_name_b)
            try:
                ipdb.interfaces[if_name].remove()
                logging.info('Removed stray interface {}'.format(if_name))
            except KeyError as e:
                logging.debug('Stray interface {} doesn\'t exist -- not removing'.format(if_name))
        ipdb.commit()
        ipdb.release()

def prepare_namespaces(graph, dry_run=False, instance_id=None):
    """
    Create a veth-connected mesh from :data:`graph`

    :param networkx.Graph graph: The graph defining the test mesh
    :param bool dry_run: If set makes Meshinery not touch any namespaces
    :param str instance_id: If set changes the middle section of each
    namespace's name; current PID by default
    :return networkx.Graph: The same graph containing runtime attributes
    """

    if instance_id is None:
        instance_id = os.getpid()
    # Create namespaces
    for node_name in graph.nodes:
        ns_name = "meshinery-{}-{}".format(instance_id, node_name)
        logging.info('Adding namespace "{}"'.format(ns_name))
        if not dry_run:
            ns = NetNS(ns_name)
            ipdb = IPDB(nl=ns)
            ipdb.interfaces['lo'].up().commit()
            ipdb.commit()
            ipdb.release()


    # Create veth bridges
    for node_name, neigh_name in graph.edges:
        neighbors = graph[node_name]

        node = graph.node[node_name]
        neigh = graph.node[neigh_name]

        # If an edge hasn't been created yet
        node_iface = '{}-{}'.format(node_name, neigh_name)
        neigh_iface = '{}-{}'.format(neigh_name, node_name)

        node_ns = 'meshinery-{}-{}'.format(instance_id, node_name)
        neigh_ns = 'meshinery-{}-{}'.format(instance_id, neigh_name)

        if not dry_run:
            node_ns_handle = NetNS(node_ns)
            neigh_ns_handle = NetNS(neigh_ns)

            ipdb = IPDB()

            # Create namespace-aware IPDB handles
            node_ipdb = IPDB(nl=node_ns_handle)
            neigh_ipdb = IPDB(nl=neigh_ns_handle)

            # Create a veth pair
            ipdb.create(ifname=node_iface, kind='veth',
                    peer=neigh_iface).commit()

            # Assign node IP
            ipdb.interfaces[node_iface]['net_ns_fd'] = node_ns
            ipdb.commit()
            node_ipdb.interfaces[node_iface].add_ip(node['ip'])
            node_ipdb.interfaces[node_iface].up().commit()

            # Assign neighbor IP
            ipdb.interfaces[neigh_iface].add_ip(neigh['ip'])
            ipdb.interfaces[neigh_iface]['net_ns_fd'] = neigh_ns
            ipdb.commit()
            neigh_ipdb.interfaces[neigh_iface].add_ip(neigh['ip'])
            neigh_ipdb.interfaces[neigh_iface].up().commit()

            ipdb.release()

            node_ipdb.release()
            neigh_ipdb.release()

        logging.debug('Created %s and %s interfaces' % (node_iface, neigh_iface))

    ipdb.release()

def main():
    """
    The entrypoint for the program.
    """
    args = docopt(USAGE)

    if args['--verbose']:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    logging.info("Starting")

    logging.debug("Args:\n{}".format(pformat(args)))

    agraph = AGraph(args['DOT_GRAPH_FILE'])
    graph = nx.Graph(agraph)

    clean(graph, dry_run=args['--dry-run'], instance_id=args['--id'], strays=args['--strays'])
    if args['clean']:
        return

    prepare_namespaces(graph, dry_run=args['--dry-run'], instance_id=args['--id'])


if __name__ == '__main__':
    main()
