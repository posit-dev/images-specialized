# Posit Workbench for Google Cloud Workstations

This container image packages [Workbench](https://docs.posit.co/ide/server-pro/) as a [Google Cloud Workstations](https://cloud.google.com/workstations) configuration image. It builds on Google's predefined workstation base image and adds Workbench, R, Python, Jupyter, and the workstation startup hooks needed to launch Workbench when a user starts their workstation.

## Setup

Use Google Cloud's documentation to attach this image to a workstation configuration:

[**Develop code using Posit Workbench (RStudio) on Google Cloud Workstations**](https://docs.cloud.google.com/workstations/docs/develop-code-using-posit-workbench-rstudio)

That guide covers creating a workstation cluster and configuration, selecting the Workbench image, and connecting users.

## Image registries

The image is published to Google Artifact Registry. Pull from the location closest to your workstation cluster:

| Region   | Repository                                                                  |
|----------|-----------------------------------------------------------------------------|
| Multi-region (US) | `us-docker.pkg.dev/posit-images/cloud-workstations/workbench`      |
| US Central        | `us-central1-docker.pkg.dev/posit-images/cloud-workstations/workbench` |
| Europe            | `europe-docker.pkg.dev/posit-images/cloud-workstations/workbench`  |
| Asia              | `asia-docker.pkg.dev/posit-images/cloud-workstations/workbench`    |

A mirror is also available at [`ghcr.io/posit-dev/workbench-for-google-cloud-workstations`](https://github.com/posit-dev/images-specialized/pkgs/container/workbench-for-google-cloud-workstations) for inspection and local builds.

## Image variants

| Variant | Description |
|---------|-------------|
| `std` (Standard) | Opinionated image with R, Python, Jupyter, and Workbench preinstalled. Runs as a workstation out of the box. |
| `min` (Minimal)  | Smaller image you can extend with your own R, Python, and system dependencies. Will not run as-is. |

## Image tags

Tags follow `{version}-{os}[-{variant}]`. For example:

- `2026.01.2-418.pro1-ubuntu-24.04` — Standard variant on Ubuntu 24.04
- `2026.01.2-418.pro1-ubuntu-24.04-std` — Standard variant (explicit)
- `2026.01.2-418.pro1-ubuntu-24.04-min` — Minimal variant
- `latest` — Most recent Standard build on the default OS

Browse the [Artifact Registry repository](https://us-docker.pkg.dev/posit-images/cloud-workstations/workbench) for the full tag list.

## Installed software

| Component | Path                              |
|-----------|-----------------------------------|
| Workbench | `/usr/lib/rstudio-server/`        |
| R         | `/opt/R/{version}/bin/R`          |
| Python    | `/opt/python/{version}/bin/python3` (symlinked at `/opt/python/default`) |
| JupyterLab | `/opt/python/jupyter/bin/jupyter` |
| Quarto    | `/usr/local/bin/quarto`           |
| TinyTeX   | Installed via Quarto              |

User authentication, home-directory persistence, and the Workbench license are managed by the workstation runtime — see Google's documentation linked above.

## Exposed ports

| Port | Description       |
|------|-------------------|
| 8787 | HTTP web interface |
| 5559 | Job Launcher      |

## Caveats

### Security

Review this image before production use. If your organization has specific Common Vulnerabilities and Exposures (CVE) or vulnerability requirements, rebuild the image to meet your security standards.

Posit rebuilds the published image weekly to include operating system patches.

### Base image

This image is built on `us-central1-docker.pkg.dev/cloud-workstations-images/predefined/base:public-image-current` and inherits its lifecycle. Some behaviors (workstation startup, user provisioning, persistent home directories) are provided by the base image and the workstation runtime, not by Workbench.

## Documentation

- [Posit Workbench documentation](https://docs.posit.co/ide/server-pro/)
- [Google Cloud Workstations documentation](https://cloud.google.com/workstations/docs)
- [Develop code using Posit Workbench on Google Cloud Workstations](https://docs.cloud.google.com/workstations/docs/develop-code-using-posit-workbench-rstudio)
