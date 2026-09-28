#!/usr/bin/env bash
# Remove only preview SageMaker versions whose private ECR image has expired.
set -euo pipefail

REGION=us-east-2
ACCOUNT=935931255537
IMAGE=positron-sagemaker-preview
ECR_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${IMAGE}"

if [[ "$(aws sts get-caller-identity --region "$REGION" --query Account --output text)" != "$ACCOUNT" ]]; then
  echo "::error::Not authenticated to the preview image account ($ACCOUNT)." >&2
  exit 1
fi

# Only ImageNotFoundException proves that ECR removed the image. Fail closed on
# AccessDenied, throttling, or other API failures.
ecr_state() {
  local result
  if result=$(aws ecr describe-images --repository-name "$IMAGE" \
      --image-ids "$1" --region "$REGION" \
      --query 'imageDetails[0].imageDigest' --output text 2>&1); then
    echo present
  elif [[ "$result" == *ImageNotFoundException* ]]; then
    echo missing
  else
    echo "::error::ECR check failed: $result" >&2
    return 1
  fi
}

# Spaces, running apps and explicit domain/profile version pins must not lose
# their catalog entry just because an ECR lifecycle rule deleted the image.
version_is_referenced() {
  local version="$1" domain domain_images profiles spaces apps name space user app_arn
  local version_arn="arn:aws:sagemaker:${REGION}:${ACCOUNT}:image-version/${IMAGE}/${version}"
  local args=()

  for domain in $(jq -r '.[]' <<<"$DOMAINS"); do
    domain_images=$(aws sagemaker describe-domain --domain-id "$domain" \
      --region "$REGION" --query 'DefaultUserSettings.JupyterLabAppSettings.CustomImages' --output json) || exit 1
    if jq -e --arg image "$IMAGE" --argjson version "$version" \
        'any(.[]?; .ImageName == $image and .ImageVersionNumber == $version)' \
        <<<"$domain_images" >/dev/null; then
      return 0
    fi

    profiles=$(aws sagemaker list-user-profiles --domain-id "$domain" \
      --region "$REGION" --query 'UserProfiles[].UserProfileName' --output json) || exit 1
    for name in $(jq -r '.[]' <<<"$profiles"); do
      domain_images=$(aws sagemaker describe-user-profile --domain-id "$domain" \
        --user-profile-name "$name" --region "$REGION" \
        --query 'UserSettings.JupyterLabAppSettings.CustomImages' --output json) || exit 1
      if jq -e --arg image "$IMAGE" --argjson version "$version" \
          'any(.[]?; .ImageName == $image and .ImageVersionNumber == $version)' \
          <<<"$domain_images" >/dev/null; then
        return 0
      fi
    done

    spaces=$(aws sagemaker list-spaces --domain-id "$domain" --region "$REGION" \
      --query 'Spaces[].SpaceName' --output json) || exit 1
    for name in $(jq -r '.[]' <<<"$spaces"); do
      app_arn=$(aws sagemaker describe-space --domain-id "$domain" \
        --space-name "$name" --region "$REGION" \
        --query 'SpaceSettings.JupyterLabAppSettings.DefaultResourceSpec.SageMakerImageVersionArn' \
        --output text) || exit 1
      [[ "$app_arn" == "$version_arn" ]] && return 0
    done

    apps=$(aws sagemaker list-apps --domain-id "$domain" --region "$REGION" \
      --query 'Apps[?AppType==`JupyterLab`].{space:SpaceName,user:UserProfileName,name:AppName}' \
      --output json) || exit 1
    while IFS= read -r app; do
      name=$(jq -r '.name' <<<"$app")
      space=$(jq -r '.space // empty' <<<"$app")
      user=$(jq -r '.user // empty' <<<"$app")
      args=(--domain-id "$domain" --app-type JupyterLab --app-name "$name")
      if [[ -n "$space" ]]; then
        args+=(--space-name "$space")
      elif [[ -n "$user" ]]; then
        args+=(--user-profile-name "$user")
      else
        echo "::error::JupyterLab app $name has no Space or user profile." >&2
        exit 1
      fi
      app_arn=$(aws sagemaker describe-app "${args[@]}" --region "$REGION" \
        --query 'ResourceSpec.SageMakerImageVersionArn' --output text) || exit 1
      [[ "$app_arn" == "$version_arn" ]] && return 0
    done < <(jq -c '.[]' <<<"$apps")
  done
  return 1
}

DOMAINS=$(aws sagemaker list-domains --region "$REGION" \
  --query 'Domains[].DomainId' --output json)
VERSIONS=$(aws sagemaker list-image-versions --image-name "$IMAGE" \
  --region "$REGION" --query 'ImageVersions[].[Version,ImageVersionStatus]' --output json)

while read -r version status; do
  [[ "$status" == CREATED ]] || continue
  base=$(aws sagemaker describe-image-version --image-name "$IMAGE" \
    --version-number "$version" --region "$REGION" --query BaseImage --output text)
  case "$base" in
    "$ECR_URI":*) image_id="imageTag=${base#"$ECR_URI":}" ;;
    "$ECR_URI"@sha256:*) image_id="imageDigest=sha256:${base#"$ECR_URI"@sha256:}" ;;
    *) echo "Skipping preview version $version: unexpected base image $base"; continue ;;
  esac
  [[ "$image_id" != 'imageTag=' && "$image_id" != 'imageDigest=sha256:' ]] || exit 1
  state=$(ecr_state "$image_id")
  [[ "$state" == missing ]] || continue

  if version_is_referenced "$version"; then
    echo "::warning::Preview version $version has no ECR image but is still referenced; leaving it."
    continue
  fi
  # A concurrent push may have restored the tag while we checked Spaces.
  state=$(ecr_state "$image_id")
  [[ "$state" == missing ]] || continue

  if output=$(aws sagemaker delete-image-version --image-name "$IMAGE" \
      --version-number "$version" --region "$REGION" 2>&1); then
    echo "Deleted preview version $version (missing $image_id in ECR)."
  elif [[ "$output" == *ResourceInUse* ]]; then
    echo "::warning::Preview version $version is still in use; leaving it."
  else
    echo "::error::Could not delete preview version $version: $output" >&2
    exit 1
  fi
done < <(jq -r '.[] | @tsv' <<<"$VERSIONS")
