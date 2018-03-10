#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# External imports
from pyroute2 import IPDB, netns

# Standard library imports
import argparse
import sys


layout_dict = {
    'n1': {
        'neighbors': [
            'n2',
        ],
        'neighbors_connected': [],
        'ip': '1.0.0.1',
    },
    'n2': {
        'neighbors': [
            'n3',
        ],
        'neighbors_connected': [],
        'ip': '1.0.0.2',
    },
    'n3': {
        'neighbors': [
            'n4',
        ],
        'neighbors_connected': [],
        'ip': '1.0.0.3',
    },
    'n4': {
        'neighbors': [
            'n1',
        ],
        'neighbors_connected': [],
        'ip': '1.0.0.4',
    }
}


def parse_args(args):
    """
    Parse the supplied arg list
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('JSON', help='A JSON file containing te layout of' +
                        'your mesh')
    parser.add_argument('--verbose', help='Be verbose', action='store_true',
                        default=False)
    parser.add_argument('--dry-run', help='Don\'t touch actual namespaces '
                        + 'or interfaces', action='store_true', default=False)
    parser.add_argument('--clean', help='Remove the namespaces listed in JSON',
                        action='store_true', default=False)

    return parser.parse_args(args)


def clean_namespaces(namespaces, verbose=False, dry_run=False):
    """
    Remove namespaces listed in :data:`namespaces`

    :param list(str) namespaces: The namespaces to delete
    """
    for ns in namespaces:
        try:
            if not dry_run:
                netns.remove(ns)
            if verbose:
                print('Removed namespace %s' % ns)
        except FileNotFoundError as e:
            if verbose:
                print('Namespace %s doesn\'t exist - not removing' % ns)


def main():
    """
    The CLI entrypoint
    """
    args = parse_args(sys.argv[1:])
    layout = layout_dict

    ip = IPDB()

    # Clean up pre-existing namespaces
    clean_namespaces(layout.keys(), verbose=args.verbose, dry_run=args.dry_run)

    if args.clean:
        return

    # Create namespaces
    for node_name in layout.keys():
        if args.verbose:
            print('Adding namespace "%s"' % node_name)
        if not args.dry_run:
            netns.create(node_name)

    # Create veth bridges
    for node_name, node in layout.items():
        for neigh_name in node['neighbors']:

            # If an edge hasn't been created yet
            if neigh_name not in node['neighbors_connected']:
                node_iface = '%s-%s' % (node_name, neigh_name)
                neigh_iface = '%s-%s' % (neigh_name, node_name)

                if not args.dry_run:
                    # Create a veth pair
                    ip.create(ifname=node_iface, kind='veth',
                              peer=neigh_iface).commit()

                    # Assign node IP
                    ip.interfaces[node_iface].add_ip(node['ip'], 32)
                    ip.interfaces[node_iface]['net_ns_fd'] = node_name
                    ip.interfaces[node_iface].up()

                    # Assign neighbor IP
                    ip.interfaces[node_iface].add_ip(layout[neigh_name]['ip'],
                                                     32)
                    ip.interfaces[neigh_iface]['net_ns_fd'] = neigh_name
                    ip.interfaces[neigh_iface].up()

                    ip.commit()

                if args.verbose:
                    print('Created %s and %s' % (node_iface, neigh_iface))

                node['neighbors_connected'].append(neigh_name)
                layout[neigh_name]['neighbors_connected'].append(node_name)

    ip.release()


if __name__ == '__main__':
    main()
