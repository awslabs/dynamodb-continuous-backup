#!/bin/bash

ver=1.5

# Fail on any error:
set -e

# validate that the config file exists
if [ ! -f $1 ]; then
	echo "$1 is not a valid file"
	usage
fi

# save the configuration file to the config.loc file
if [ $# -ne 1 ]; then
	echo "Proceeding without a supplied configuration file. You must use the provided SAM or configure the backup Lambda function manually."
else
	echo $1 | tr -d '\n' > config.loc
fi

if [ ! -d ../dist ]; then
	mkdir ../dist
fi

ARCHIVE=dynamodb_continuous_backup-$ver.zip

# add required dependencies
if [ ! -d lib/hjson ]; then
	pip install hjson -t lib
fi

if [ ! -d lib/shortuuid ]; then
	pip install shortuuid -t lib
fi

# bin the old zipfile
if [ -f ../dist/$ARCHIVE ]; then
	echo "Removed existing Archive ../dist/$ARCHIVE"
	rm -Rf ../dist/$ARCHIVE
fi

cmd="zip -r ../dist/$ARCHIVE index.py dynamo_continuous_backup.py lib/"

if [ $# -eq 1 ]; then
	cmd=`echo $cmd config.loc $1`
fi

echo $cmd

eval $cmd

echo "Generated new Lambda Archive ../dist/$ARCHIVE"
