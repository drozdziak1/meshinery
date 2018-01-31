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
    },
    'n2': {
        'neighbors': [
            'n3',
        ],
        'neighbors_connected': [],
    },
    'n3': {
        'neighbors': [
            'n4',
        ],
        'neighbors_connected': [],
    },
    'n4': {
        'neighbors': [
            'n1',
        ],
        'neighbors_connected': [],
    }
}


def parse_args(args):
    """
    Parse the supplied arg list
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('JSON', help='A JSON file containing te layout of' +
                        'your mesh')

    return parser.parse_args(args)


def main():
    """
    The CLI entrypoint
    """
    args = parse_args(sys.argv[1:])

    layout = layout_dict

    # Create namespaces
    for node_name in layout.keys():
        print('Adding namespace "%s"' % node_name)

    # Create veth bridges
    for node_name, node in layout.items():
        for neigh_name in node['neighbors']:
            if neigh_name not in node['neighbors_connected']:
                node_iface = '%s-%s' % (node_name, neigh_name)
                neigh_iface = '%s-%s' % (neigh_name, node_name)
                print('Adding %s and %s' % (node_iface, neigh_iface))
                node['neighbors_connected'].append(neigh_name)
                layout[neigh_name]['neighbors_connected'].append(node_name)

    # Destroy namespaces
    for node_name in layout.keys():
        print('Removing namespace "%s"' % node_name)

    print('args.JSON = %s' % args.JSON)


if __name__ == '__main__':
    main()
