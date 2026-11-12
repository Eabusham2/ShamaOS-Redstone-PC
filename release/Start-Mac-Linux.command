#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! python3 -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
  echo "Please install Python 3.11 or newer from https://www.python.org/downloads/ and try again."
  exit 1
fi
if [ ! -x .installer-env/bin/python ]; then
  python3 -m venv .installer-env
fi
.installer-env/bin/python -m pip install 'nbtlib>=2.0,<3'
.installer-env/bin/python Install-ShamaOS.py "$@"
