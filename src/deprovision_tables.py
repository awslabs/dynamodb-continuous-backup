#!/usr/bin/env python

import sys
import setup_existing_tables

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print "Usage: deprovision_tables.py whitelist_configuration.hjson"
        sys.exit(-1)
    else:
        setup_existing_tables.deprovision(sys.argv[1])