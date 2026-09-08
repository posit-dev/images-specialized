# sagemaker-mirror

Mirrors a specific tag of `public.ecr.aws/posit/positron-sagemaker` to a
private Amazon ECR repository and registers a SageMaker ImageVersion. Each
step is skipped independently if it has already been done, so the action is
safe to call on a schedule.

The copy is skipped when the tag is in ECR. The registration is skipped when
the tag is already an ImageVersion alias. Checking these separately matters:
a tag can reach ECR and still have no ImageVersion, for example when an
earlier run failed after the copy. A single ECR check would skip that tag on
every later run and never register it.

Uses `oras copy --recursive` to preserve the full OCI artifact tree,
including the SOCI index that SageMaker Studio uses for lazy image loading.
`docker pull/push` would strip the SOCI index and collapse the image index
to a single manifest.

Polls `describe-image-version` after registration until the version reaches
`CREATED` or `CREATE_FAILED`. `create-image-version` returns exit 0
regardless of whether the image is reachable in ECR; the failure surfaces
asynchronously and would silently leave the job green without this check.

## Usage

```yaml
- name: Configure AWS credentials
  uses: aws-actions/configure-aws-credentials@...
  with:
    role-to-assume: ${{ secrets.SAGEMAKER_MIRROR_ROLE_ARN }}
    aws-region: us-east-1

- name: Mirror
  uses: posit-dev/images-specialized/.github/actions/sagemaker-mirror@main
  with:
    tag: "2026.09.0-124"
    target-ecr-registry: 123456789012.dkr.ecr.us-east-1.amazonaws.com
```

The calling workflow is responsible for configuring AWS credentials before
calling this action. See [IAM permissions](#iam-permissions) for what the
role must allow.

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `tag` | Yes | — | Version tag to mirror (e.g. `2026.09.0-124`). |
| `source-image` | No | `public.ecr.aws/posit/positron-sagemaker` | Public ECR image URI without tag. |
| `target-ecr-registry` | Yes | — | Private ECR registry host (e.g. `123456789012.dkr.ecr.us-east-1.amazonaws.com`). The AWS region and account ID are derived from this value and used for all ECR and SageMaker API calls. |
| `target-repository` | No | `positron-sagemaker` | Repository name in the private ECR registry. |
| `sagemaker-image-name` | No | `positron-sagemaker` | Name of the SageMaker catalog image to register versions against. |

## Outputs

| Output | Description |
|---|---|
| `copied` | `true` if the image was copied into ECR; `false` if it was already there. |
| `registered` | `true` if a SageMaker ImageVersion was registered; `false` if the tag was already registered. |

The tag is added as an ImageVersion alias after the version reaches
`CREATED`, never at create time. An alias passed to `create-image-version`
stays attached to a `CREATE_FAILED` version, where it can be neither removed
nor reused, so the tag could not be registered again without deleting the
failed version first.

## IAM permissions

The role assumed by the calling workflow needs the following permissions.
The infrastructure that manages this role for the platform team is in
`posit-dev/platform-infra` (`images_infra/sagemaker.py`,
`_mirror_role()`).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AuthorizeRegistry",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "PushAndDescribeImages",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeImages",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:<region>:<account>:repository/<target-repository>"
    },
    {
      "Sid": "RegisterImageVersions",
      "Effect": "Allow",
      "Action": [
        "sagemaker:CreateImageVersion",
        "sagemaker:DescribeImageVersion",
        "sagemaker:ListAliases",
        "sagemaker:ListImageVersions",
        "sagemaker:UpdateImageVersion"
      ],
      "Resource": [
        "arn:aws:sagemaker:<region>:<account>:image/<sagemaker-image-name>",
        "arn:aws:sagemaker:<region>:<account>:image-version/<sagemaker-image-name>/*"
      ]
    }
  ]
}
```
