#!/usr/bin/env python

import sys
import setup_existing_tables as setup


if __name__ == "__main__":
    setup.provision(sys.argv[1])
