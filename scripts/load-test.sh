#!/bin/sh
set -eu

exec python -m coal_platform.load_test "$@"
