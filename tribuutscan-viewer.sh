#!/bin/sh
# triBUUTSCAN live colour + IR + depth viewer (Creality Ferret Pro).
repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit
cd -- "$repo" && exec python3 -m tribuutscan.viewer "$@"
