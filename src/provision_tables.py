#!/usr/bin/env python

import sys

# add the lib directory to the path
sys.path.append('lib')

import setup_existing_tables as setup
import argparse

if __name__ == "__main__":
        parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument('whitelist_configuration', help='whitelist_configuration.hjson')
        args = parser.parse_args()

        setup.provision(args.whitelist_configuration)
