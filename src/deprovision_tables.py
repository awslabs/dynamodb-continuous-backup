#!/usr/bin/env python

import sys
import setup_existing_tables


if __name__ == "__main__":
    setup_existing_tables.deprovision(sys.argv[1])
