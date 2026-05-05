#!/bin/bash
set -euo pipefail

# Output delimiter
d="===="

# Update apt repositories
apt-get update -yq

echo "$d Install Posit Workbench 2026.04.0+526.pro2 $d"

RSTUDIO_INSTALL_NO_LICENSE_INITIALIZATION=1 apt-get install -yf rstudio-server=2026.04.0+526.pro2
apt-mark hold rstudio-server

# Clean up
apt-get clean -yqq && \
rm -rf /var/lib/apt/lists/*
