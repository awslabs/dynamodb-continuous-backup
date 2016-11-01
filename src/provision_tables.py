#!/usr/bin/env python

import sys
import setup_existing_tables as setup

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print "Usage: provision_tables.py whitelist_configuration.hjson"
        sys.exit(-1)
    else:
        setup.provision(sys.argv[1])
