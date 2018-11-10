#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# External imports
import networkx as nx

from docopt import docopt
from pygraphviz import AGraph
from pyroute2 import IPDB, netns, NetNS, NSPopen

# Standard library imports
import argparse
import json
import logging
import os
import signal
import subprocess
import shlex
import sys
import time

from pprint import pformat

DEFAULT_COMMAND_EXIT_TIMEOUT = 0.2 # 200ms; passed to Popen.wait()

USAGE = """
Meshinery - the mesh network testing toolkit.

Usage:
    meshinery DOT_GRAPH_FILE [--id ID] [--clean --dry-run --verbose --strays]
    meshinery clean DOT_GRAPH_FILE --id ID [--dry-run --verbose --strays]
    meshinery -h | --help

Options:
    --clean         Remove the namespaces associated with DOT_GRAPH_FILE and id before running
    --dry-run       Dry run mode.
    --id ID         Set a name for the Meshinery instance to use
    --strays        When cleaning up, attempt to remove all interfaces from the global namespace
    --verbose       Log at debug level.
    -h --help       Show this screen.
    DOT_GRAPH_FILE  A file containing the description of the mesh to emulate.
"""

# Internal handles for the graph in use and CLI args for easier cleanup; only
# used when `meshinery` is run from the CLI; not intended for outside use
_MESHINERY_GRAPH=None
_MESHINERY_ARGS=None

def clean(graph, dry_run=False, instance_id=None, strays=False):
    """
    Remove namespaces listed in :data:`graph` and stop scripts

    :param networkx.Graph graph: The graph for which we want to clean up
    :param bool dry_run: If set makes Meshinery not touch any namespaces
    :param str instance_id: If set changes the middle section of each
    namespace's name; current PID by default

    :return networkx.Graph: The same graph after cleanup
    """
    if instance_id is None:
        instance_id = os.getpid()

    # Remove namespaces and kill running commands if applicable
    for node_name in graph:
        node = graph.node[node_name]
        netns_name = 'meshinery-{}-{}'.format(instance_id, node_name)
        try:
            if not dry_run:
                netns.remove(netns_name)
                logging.info('Removed namespace {}'.format(netns_name))

        # The namespace does not exist
        except FileNotFoundError as e:
            logging.debug('Namespace {} doesn\'t exist - not removing'.format(netns_name))

        # Grab the per-node command and see if it's still running
        cmd_handle = node.get('command_handle')
        if cmd_handle is not None:
            if cmd_handle.poll() is not None:
                logging.warn('{}: "{}" was not running at cleanup time'.format(node_name, node['command']))

            # Play nice - send initial SIGTERM
            cmd_handle.terminate()
            logging.debug('{}: Sent SIGTERM to PID {} ("{}")'.format(
                node['netns'], cmd_handle.pid, node['command']
                )
                )

            # Wait the specified/default time before nuclear option
            command_exit_delay = node.get('command_exit_delay',
                    DEFAULT_COMMAND_EXIT_TIMEOUT)
            time.sleep(command_exit_delay)

            # Send the SIGKILL if applicable
            if cmd_handle.poll() is None:
                cmd_handle.kill()
                logging.debug('{nn}: "{cmd}" was SIGKILLed (was PID {pid})'.format(nn=node_name, cmd=node['command'], pid=cmd_handle.pid))
            else:
                logging.debug('{nn}: "{cmd}" quit gracefully. (was PID {pid})'.format(nn=node_name, cmd=node['command'], pid=cmd_handle.pid))

            stdout, stderr = cmd_handle.communicate()
            logging.debug('{nn}: "{cmd}" stdout:\n{out}'.format(nn=node_name,
                cmd=node['command'], out=str(stdout, 'utf-8')))
            logging.debug('{nn}: "{cmd}" stderr:\n{err}'.format(nn=node_name,
                cmd=node['command'], err=str(stderr, 'utf-8')))
        else:
            logging.debug('{}: No command to stop.'.format(node_name))


    # Remove stray interfaces
    if strays:
        ipdb = IPDB()
        for node_name_a, node_name_b in graph.edges:
            if_name = '{}-{}'.format(node_name_a, node_name_b)
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
        ns_name = 'meshinery-{}-{}'.format(instance_id, node_name)
        logging.info('Adding namespace "{}"'.format(ns_name))
        if not dry_run:
            # Establish the namespace
            ns = NetNS(ns_name)
            ipdb = IPDB(nl=ns)
            ipdb.interfaces['lo'].up().commit()
            ipdb.commit()
            ipdb.release()

            # Enable forwarding
            sysctl_cmd = shlex.split('sysctl -w net.ipv4.conf.all.forwarding=1 net.ipv6.conf.all.forwarding=1')
            subprocess.run(sysctl_cmd, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE).check_returncode()

            graph.node[node_name]['netns'] = ns_name
            graph.node[node_name]['interfaces'] = [] # Needed so that we can safely append later

    # Create veth bridges
    for node_name, neigh_name in graph.edges:
        neighbors = graph[node_name]

        node = graph.node[node_name]
        neigh = graph.node[neigh_name]

        # If an edge hasn't been created yet
        node_iface = '{}-{}'.format(node_name, neigh_name)
        neigh_iface = '{}-{}'.format(neigh_name, node_name)

        if not dry_run:
            node_ns_handle = NetNS(node['netns'])
            neigh_ns_handle = NetNS(neigh['netns'])

            ipdb = IPDB()

            # Create namespace-aware IPDB handles
            node_ipdb = IPDB(nl=node_ns_handle)
            neigh_ipdb = IPDB(nl=neigh_ns_handle)

            # Create a veth pair
            ipdb.create(ifname=node_iface, kind='veth',
                    peer=neigh_iface).commit()

            # Assign node IP
            ipdb.interfaces[node_iface]['net_ns_fd'] = node['netns']
            ipdb.commit()
            node_ipdb.interfaces[node_iface].add_ip(node['ip'])
            node_ipdb.interfaces[node_iface].up().commit()

            # Assign neighbor IP
            ipdb.interfaces[neigh_iface].add_ip(neigh['ip'])
            ipdb.interfaces[neigh_iface]['net_ns_fd'] = neigh['netns']
            ipdb.commit()
            neigh_ipdb.interfaces[neigh_iface].add_ip(neigh['ip'])
            neigh_ipdb.interfaces[neigh_iface].up().commit()

            ipdb.release()

            node_ipdb.release()
            neigh_ipdb.release()

            node['interfaces'].append(node_iface)
            neigh['interfaces'].append(neigh_iface)

        logging.debug('Created %s and %s interfaces' % (node_iface, neigh_iface))

    ipdb.release()

def execute(graph, dry_run=False, instance_id=None):
    """
    Fire up whatever's in each node's ``command`` attribute.

    Note: The returned graph contains handles for the commands specified inside

    :param networkx.Graph graph: The graph to run the scripts for
    :param bool dry_run: If set makes Meshinery not touch any namespaces
    :param str instance_id: If set changes the middle section of each
    namespace's name; current PID by default
    :return networkx.Graph: The same graph but with script runtime annotations
    """

    if instance_id is None:
        instance_id = os.getpid()

    for node_name in graph.nodes:
        node = graph.nodes[node_name]

        if node.get('command') is None:
            logging.info('"command" not defined for node {}'.format(node_name))
            continue

        # Turn node attributes into a JSON for the command
        attribs = dict(node)
        attribs['node_name'] = node_name
        attribs['neighs'] = dict(graph[node_name])

        attrib_string = json.dumps(attribs) + '\n'

        # TODO:
        # * Provide every per-node command with complete optimum paths to other
        # nodes relevant to all metric attributes

        logging.info('{}: running "{}"'.format(node_name, node['command']))

        command_line = shlex.split(node['command'])

        # Start the command in the node's namespace netns
        try:
            node['command_handle'] = NSPopen(node['netns'],
                    command_line,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
        except Exception as e:
            logging.debug('Exception "{}" was thrown while running "{}"'.format(e, node['command']))
            if type(e) is FileNotFoundError:
                logging.error('Executable "{}" does not exist!'.format(command_line[0]))
                logging.debug('PATH="{}"'.format(os.environ['PATH']))
            raise e

        node['command_handle'].stdin.write(bytes(attrib_string, encoding='utf-8'))
        node['command_handle'].stdin.flush()

    return graph


def handle_sigint(signal, frame):
    global _MESHINERY_GRAPH
    global _MESHINERY_ARGS
    logging.info('Received interrupt, cleaning up...')
    clean(_MESHINERY_GRAPH, dry_run=_MESHINERY_ARGS['--dry-run'],
            instance_id=_MESHINERY_ARGS['--id'], strays=_MESHINERY_ARGS['--strays'])
    exit(0)


def main():
    """
    The entrypoint for the program.
    """
    global _MESHINERY_GRAPH
    global _MESHINERY_ARGS

    # Register the keyboard interrupt and SIGTERM handler
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    args = docopt(USAGE)
    _MESHINERY_ARGS=args

    if args['--verbose']:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    logging.info('Starting')
    logging.debug('Args:\n{}'.format(pformat(args)))

    agraph = AGraph(args['DOT_GRAPH_FILE'])
    graph = nx.Graph(agraph)
    _MESHINERY_GRAPH=graph # We intentionally don't take a fresh copy

    if args['--clean']:
        clean(graph, dry_run=args['--dry-run'], instance_id=args['--id'], strays=args['--strays'])

    if args['clean']:
        return

    # Arrange network namespaces and crate veth bridges
    prepare_namespaces(graph, dry_run=args['--dry-run'], instance_id=args['--id'])

    # Run commands associated with each node
    try:
        graph = execute(graph, dry_run=args['--dry-run'], instance_id=args['--id'])
        _MESHINERY_GRAPH=graph
    except Exception as e:
        logging.error('Per-node command execution failed, cleaning up...')
        logging.debug('Caught exception {} in main(): {}'.format(type(e), e))
        clean(graph, dry_run=args['--dry-run'], instance_id=args['--id'])
        sys.exit(1)

    logging.info("Meshinery is ready. Press any key to clean up and exit.")
    input()

    clean(graph, dry_run=args['--dry-run'], instance_id=args['--id'], strays=args['--strays'])
    logging.info('Bye!')
