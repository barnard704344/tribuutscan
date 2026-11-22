#!/bin/sh
# triBUUTSCAN browser interface and local scanner host (Creality Ferret Pro).
repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit
cd -- "$repo" && exec python3 -m tribuutscan.webapp "$@"
