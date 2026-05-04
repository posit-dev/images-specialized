# Posit Workbench Container Images for Specialized Environments

Container images that package [Workbench](https://docs.posit.co/ide/server-pro) for specific managed cloud platforms. Each image arrives configured for the host platform's authentication, storage, and lifecycle conventions, so you can use it directly as the workstation or compute image.

For the standard, non-platform-specific Workbench container images, see [posit-dev/images-workbench](https://github.com/posit-dev/images-workbench).

## Images

| Image | Platform | Platform documentation |
|:------|:---------|:-----------------------|
| [workbench-for-google-cloud-workstations](./workbench-for-google-cloud-workstations/) | Google Cloud Workstations | [Develop code using Posit Workbench](https://docs.cloud.google.com/workstations/docs/develop-code-using-posit-workbench-rstudio) |
| [workbench-for-microsoft-azure-ml](./workbench-for-microsoft-azure-ml/) | Azure Machine Learning compute instances | [Add custom applications such as RStudio or Posit Workbench](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-compute-instance?view=azureml-api-2&tabs=python#add-custom-applications-such-as-rstudio-or-posit-workbench) |

See each image's documentation for the canonical registry, available tags, and platform-specific configuration.

## Prerequisites

| Tool | Required for | Install |
|------|--------------|---------|
| [Docker](https://docs.docker.com/get-docker/) | Building and running containers locally | [Get Docker](https://docs.docker.com/get-docker/) |
| Product license | Running Workbench | [Licensing FAQ](https://docs.posit.co/licensing/licensing-faq.html) |

To consume the published images on their target platforms, follow the platform documentation linked above. You do not need a local Docker installation.

## Building from source

You can interact with this repository in multiple ways:

* [Build container images directly](#build) from the Containerfile.
* [Use the `bakery` CLI](#using-bakery) to manage and build container images.

## Build

You can build Open Container Initiative (OCI) container images from the definitions in this repository using one of the following container build tools:

* [buildah](https://github.com/containers/buildah/blob/main/install.md)
* [docker buildx](https://github.com/docker/buildx#installing)

Each Containerfile uses the root of the repository as its build context. The [`bakery.yaml`](https://github.com/posit-dev/images-shared/blob/main/posit-bakery/CONFIGURATION.md#bakery-configuration) project file is in the root of this repository.

```shell
PWB_VERSION="2026.01"

# Build the Google Cloud Workstations standard image using docker
docker buildx build \
    --tag workbench-for-google-cloud-workstations:${PWB_VERSION} \
    --file workbench-for-google-cloud-workstations/${PWB_VERSION}/Containerfile.ubuntu2404.std \
    .

# Build the Azure ML standard image using buildah
buildah build \
    --tag workbench-for-microsoft-azure-ml:${PWB_VERSION} \
    --file workbench-for-microsoft-azure-ml/${PWB_VERSION}/Containerfile.ubuntu2404.std \
    .

# Build the Azure ML minimal image using podman
podman build \
    --tag workbench-for-microsoft-azure-ml:${PWB_VERSION} \
    --file workbench-for-microsoft-azure-ml/${PWB_VERSION}/Containerfile.ubuntu2404.min \
    .
```

## Using `bakery`

This repository follows the structure described in [bakery usage](https://github.com/posit-dev/images-shared/tree/main/posit-bakery#usage).

Additional documentation:
- [Configuration Reference](https://github.com/posit-dev/images-shared/blob/main/posit-bakery/CONFIGURATION.md): `bakery.yaml` schema and options
- [Templating Reference](https://github.com/posit-dev/images-shared/blob/main/posit-bakery/TEMPLATING.md): Jinja2 macros for Containerfile templates
- [CI Workflows](https://github.com/posit-dev/images-shared/blob/main/CI.md): shared GitHub Actions workflows for building and pushing images

### Prerequisites

Build prerequisites:

* [python](https://docs.astral.sh/uv/guides/install-python/)
* [uv](https://docs.astral.sh/uv/getting-started/installation/)
* [docker buildx bake](https://github.com/docker/buildx#installing)
* [gh](https://github.com/cli/cli#installation) (required while repositories are private)
* `bakery`
* `goss` and `dgoss` for running image validation tests

### Build with `bakery`

By default, bakery creates an ephemeral JSON [bakefile](https://docs.bakefile.org/en/latest/language.html) to render all containers in parallel.

```shell
bakery build
```

You can view the bake plan using `bakery build --plan`. Use the CLI flags to build only a subset of images in the project.

### Test images

After building the container images, run the test suite for all images:

```shell
bakery run dgoss
```

You can use CLI flags to limit the tests to a subset of images.

## Related repositories

This repository is part of the [Posit Container Images](https://github.com/posit-dev/images) ecosystem. The non-specialized Workbench images live in [images-workbench](https://github.com/posit-dev/images-workbench). For shared build tooling and CI workflows, see [images-shared](https://github.com/posit-dev/images-shared).

## Share your feedback

We invite you to join us on [GitHub Discussions](https://github.com/posit-dev/images/discussions) to ask questions and share feedback.

## Issues

If you encounter any issues or have any questions, please [open an issue](https://github.com/posit-dev/images-specialized/issues). We appreciate your feedback.
