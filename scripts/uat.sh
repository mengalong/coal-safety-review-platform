#!/bin/sh
set -eu

exec python -m coal_platform.uat "$@"
