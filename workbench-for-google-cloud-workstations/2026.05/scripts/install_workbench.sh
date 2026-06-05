#!/bin/bash
set -euo pipefail

# Output delimiter
d="===="

# Update apt repositories
apt-get update -yq

echo "$d Install Posit Workbench 2026.05.0+218.pro1 $d"

RSTUDIO_INSTALL_NO_LICENSE_INITIALIZATION=1 apt-get install -yf rstudio-server=2026.05.0+218.pro1
apt-mark hold rstudio-server

# Clean up
apt-get clean -yqq && \
rm -rf /var/lib/apt/lists/*
