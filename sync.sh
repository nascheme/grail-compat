#!/bin/sh
./build-docs.py
rsync -av --delete site/ nas@arctrix.com:/var/www/python.ca/cpython-grail/
