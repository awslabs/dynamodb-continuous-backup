#!/bin/bash

usage() {
	echo "build.sh <config file name>"
	exit -1
}

# save the configuration file to the config.loc file
if [ $# -ne 1 ]; then
	usage
else
	echo $1 | tr -d '\n' > config.loc
fi

if [ ! -d dist ]; then
	mkdir dist
fi

ARCHIVE=dynamodb_continuous_backup.zip

# add required dependencies
if [ ! -d lib/hjson ]; then
	pip install hjson -t lib
fi

if [ ! -d lib/shortuuid ]; then
	pip install shortuuid -t lib
fi

if [ -f ../$ARCHIVE ]; then
	rm ../$ARCHIVE
fi 

zip -r ../dist/$ARCHIVE index.py dynamo_continuous_backup.py config.loc config.hjson lib/

echo "Generated new Lambda Archive ../dist/$ARCHIVE"