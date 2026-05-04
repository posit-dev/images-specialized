# Posit Workbench for Microsoft Azure Machine Learning

This container image packages [Workbench](https://docs.posit.co/ide/server-pro/) as a custom application image for [Azure Machine Learning compute instances](https://learn.microsoft.com/en-us/azure/machine-learning/concept-compute-instance). It includes Workbench, R, Python, Jupyter, the Azure CLI with the `ml` extension, and the user-provisioning glue Azure ML expects.

## Setup

Follow Microsoft's documentation to attach this image to an Azure ML compute instance as a custom application:

[**Add custom applications such as RStudio or Posit Workbench**](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-compute-instance?view=azureml-api-2&tabs=python#add-custom-applications-such-as-rstudio-or-posit-workbench)

That guide covers creating the compute instance, registering the custom application, and connecting users.

## Image registry

The image is published to GitHub Container Registry:

- `ghcr.io/posit-dev/workbench-for-microsoft-azure-ml`

Browse the [package page](https://github.com/posit-dev/images-specialized/pkgs/container/workbench-for-microsoft-azure-ml) for the full tag list.

> [!IMPORTANT]
> An older copy of this image is published to Microsoft Container Registry / Azure Container Registry. **Do not use it.** That copy is not maintained and lags significantly behind. Always pull from `ghcr.io/posit-dev/workbench-for-microsoft-azure-ml`.

## Image variants

| Variant | Description |
|---------|-------------|
| `std` (Standard) | Opinionated image with R, Python, Jupyter, and Workbench preinstalled. Runs out of the box. |
| `min` (Minimal)  | Smaller image you can extend with your own R, Python, and system dependencies. Will not run as-is. |

## Image tags

Tags follow `{version}-{os}[-{variant}]`. For example:

- `2026.01.2-418.pro1-ubuntu-24.04` — Standard variant on Ubuntu 24.04
- `2026.01.2-418.pro1-ubuntu-24.04-std` — Standard variant (explicit)
- `2026.01.2-418.pro1-ubuntu-24.04-min` — Minimal variant
- `latest` — Most recent Standard build on the default OS

## Installed software

| Component  | Path                                                                    |
|------------|-------------------------------------------------------------------------|
| Workbench  | `/usr/lib/rstudio-server/`                                              |
| R          | `/opt/R/{version}/bin/R`                                                |
| Python     | `/opt/python/{version}/bin/python3` (symlinked at `/opt/python/default`) |
| JupyterLab | `/opt/python/jupyter/bin/jupyter`                                       |
| Quarto     | `/usr/local/bin/quarto`                                                 |
| TinyTeX    | Installed via Quarto                                                    |
| Azure CLI  | `az` with the `ml` extension preinstalled                               |
| Pro Drivers | RStudio Pro database drivers                                           |

## Configuration

Configuration is normally driven by Azure ML's custom application form (which is populated by Microsoft's onboarding documentation linked above). The image reads the following environment variables at startup:

### License activation

You must have a valid [product license](https://docs.posit.co/licensing/licensing-faq.html). Choose one method:

| Variable                | Description                              |
|-------------------------|------------------------------------------|
| `PWB_LICENSE`           | License key for activation               |
| `PWB_LICENSE_SERVER`    | URL of a floating license server         |
| `PWB_LICENSE_FILE_PATH` | Path to a mounted license file (default: `/etc/rstudio-server/license.lic`) |

### User provisioning

Azure ML mounts the user's home directory at runtime. The container creates a matching local user from these variables so the home directory is owned correctly:

| Variable        | Description                          | Default     |
|-----------------|--------------------------------------|-------------|
| `USER_NAME`     | Login name for the local user        | `azureuser` |
| `USER_PASSWORD` | Optional password for the local user | (unset)     |
| `PUID`          | UID for the local user               | `1001`      |
| `PGID`          | GID for the local user               | `1001`      |

### Other settings

| Variable                | Description                                                  | Default              |
|-------------------------|--------------------------------------------------------------|----------------------|
| `PWB_LAUNCHER`          | Enable the Job Launcher                                      | `true`               |
| `PWB_LAUNCHER_TIMEOUT`  | Launcher startup timeout in seconds                          | `10`                 |
| `STARTUP_DEBUG_MODE`    | Set to `1` for verbose startup logging                       | `0`                  |
| `DIAGNOSTIC_ENABLE`     | Enable diagnostic logging                                    | `false`              |
| `DIAGNOSTIC_DIR`        | Directory for diagnostic logs                                | `/var/log/rstudio`   |

Legacy `RSW_` and `RSP_` license variables (`RSW_LICENSE`, `RSW_LICENSE_SERVER`, `RSP_LICENSE`, `RSP_LICENSE_SERVER`, `RSW_LICENSE_FILE_PATH`) are accepted for backward compatibility. Use the `PWB_` prefix for new deployments.

## Exposed ports

| Port | Description        |
|------|--------------------|
| 8787 | HTTP web interface |
| 5559 | Job Launcher       |

## Caveats

### Security

Review this image before production use. If your organization has specific Common Vulnerabilities and Exposures (CVE) or vulnerability requirements, rebuild the image to meet your security standards.

The Standard variant runs a ClamAV scan as part of the build. Posit rebuilds the published image weekly to include operating system patches.

### License keys

License keys used in containers risk activation slot loss if the container is not gracefully stopped. The license deactivates on container exit, but ungraceful shutdowns may leave the activation slot consumed on the Posit license server. For production deployments, license files are recommended over license keys.

### Hardware locking

Posit hardware-locks license state files. Changes to MAC addresses, hostnames, or compute instance redeployments may invalidate the license state and require reactivation.

## Documentation

- [Posit Workbench documentation](https://docs.posit.co/ide/server-pro/)
- [Posit Workbench on Azure ML solutions page](https://posit.co/solutions/azure-ml)
- [Azure ML compute instance documentation](https://learn.microsoft.com/en-us/azure/machine-learning/concept-compute-instance)
- [Add custom applications such as RStudio or Posit Workbench](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-compute-instance?view=azureml-api-2&tabs=python#add-custom-applications-such-as-rstudio-or-posit-workbench)
