# sagemaker-mirror

Mirrors a specific tag of `public.ecr.aws/posit/positron-sagemaker` to a
private Amazon ECR repository and registers a SageMaker ImageVersion. Both
steps are skipped if the tag already exists in the private registry, so the
action is safe to call on a schedule.

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
| `pushed` | `true` if a new version was pushed; `false` if the tag already existed. |

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
        "sagemaker:ListImageVersions"
      ],
      "Resource": [
        "arn:aws:sagemaker:<region>:<account>:image/<sagemaker-image-name>",
        "arn:aws:sagemaker:<region>:<account>:image-version/<sagemaker-image-name>/*"
      ]
    }
  ]
}
```
