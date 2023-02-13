set dotenv-load

version := `grep "^version =" pyproject.toml |awk -F\" '{print $2}'`
container_name := "gamatrix"

# List recipes
default:
  just --list

# Increment version; pass in "major" or "minor" to bump those
bump-version type="patch":
  #!/usr/bin/env bash
  set -euo pipefail
  old_version={{version}}
  IFS=. components=(${old_version##*-})
  major=${components[0]}
  minor=${components[1]}
  patch=${components[2]}
  type={{type}}
  case $type in
    major|MAJOR)
      new_version="$((major+1)).0.0";;
    minor|MINOR)
      new_version="$major.$((minor+1)).0";;
    patch|PATCH)
      new_version="$major.$minor.$((patch+1))";;
    *)
      echo "Bad type: $type"
      echo "Valid types are major, minor, patch"
      exit 1;;
  esac
  echo "Bumping version from $old_version to $new_version"
  sed -i "s/^version =.*/version = \"$new_version\"/" pyproject.toml

# Build the container
build:
  docker build -t {{container_name}}:{{version}} -t {{container_name}}:latest .

# Run the container
run:
  PORT=${PROD_PORT:=80}
  TZ=${TZ:="America/Vancouver"}
  [ -z "$PROD_GOG_DBS" ] && echo "PROD_GOG_DBS env var must be set" && exit 1
  [ -z "$PROD_CACHE" ] && echo "PROD_CACHE env var must be set" && exit 1
  [ -z "$PROD_CONFIG" ] && echo "PROD_CONFIG env var must be set" && exit 1
  docker run \
    -d \
    --name {{container_name}} \
    --dns 1.1.1.1 \
    --dns 8.8.8.8 \
    --log-driver=journald \
    -p ${PORT}:${PORT}/tcp \
    -e TZ="$TZ" \
    -v ${PROD_GOG_DBS}:/usr/src/app/gog_dbs \
    --mount type=bind,source=${PROD_CACHE},target=/usr/src/app/.cache.json \
    --mount type=bind,source=${PROD_CONFIG},target=/usr/src/app/config/config.yaml,readonly \
    gamatrix

# Tag commit with current release version
git-tag:
  #!/usr/bin/env bash
  # Nonzero exit code means there are changes
  if [ ! "$(git diff --quiet --exit-code)" ]; then
    git commit -am "bump version"
    git tag --annotate --message="bump to version {{version}}" "{{version}}"
    git push
    git push --tags
  fi

# Run the container in dev mode
dev:
  #!/usr/bin/env bash
  set -eu -o pipefail

  # These env vars come from .env
  set_mounts() {
    if [ "${DEV_GOG_DBS}x" == "x" ]; then
      echo "WARNING: DEV_GOG_DBS not set in .env; DBs won't be available"
      db_mount=""
    else
      db_mount="-v ${DEV_GOG_DBS}:/usr/src/app/gog_dbs"
    fi

    if [ "${DEV_CONFIG}x" == "x" ]; then
      echo "WARNING: DEV_CONFIG not set in .env; config won't be available"
      config_mount=""
    else
      config_mount="-v ${DEV_CONFIG}:/usr/src/app/config.yaml"
    fi

    if [ "${DEV_CACHE}x" == "x" ]; then
      echo "WARNING: DEV_CACHE not set in .env; cache won't be available"
      cache_mount=""
    else
      cache_mount="-v ${DEV_CACHE}:/usr/src/app/.cache.json"
    fi

    # This allows the user to set their own aliases, set -o vi, etc.
    bashrc_user_mount=""
    if [ "${BASHRC_USER}x" != "x" ] && [ -e "$BASHRC_USER" ]; then
      bashrc_user_mount="-v ${BASHRC_USER}:/root/.bashrc.user"
    fi
  }

  cleanup() {
      echo "Removing old docker containers. Names will appear upon success:"
      set +e
      # This will rm itself
      docker stop $CONTAINER_NAME
      set -e
  }

  # Default to latest if env var is not set
  CONTAINER_VERSION=${CONTAINER_VERSION:=latest}
  CONTAINER_NAME={{container_name}}-dev
  CONTAINER_IMAGE={{container_name}}:${CONTAINER_VERSION}
  # Default to 8080 if not set in .env
  PORT=${DEV_PORT:=8080}

  echo "Container image: ${CONTAINER_IMAGE}"

  # Stop any accidental running copies of the build container
  cleanup
  set_mounts

  # Ensure we make it to cleanup even if there's a failure from this point
  set +e

  docker run --rm -d -t \
      --name=${CONTAINER_NAME} \
      -p ${PORT}:${PORT} \
      -v $(pwd):/usr/src/app \
      -v /var/run/docker.sock:/var/run/docker.sock \
      $bashrc_user_mount \
      $db_mount \
      $config_mount \
      $cache_mount \
      -w /usr/src/app \
      ${CONTAINER_IMAGE} \
      /bin/bash

  # Install gamatrix in editable mode
  docker exec -d \
      -w /usr/src/app \
      ${CONTAINER_NAME} \
      sh -c "python -m pip install -e .[dev]"

  # Launch container
  docker exec -it \
      -w /usr/src/app \
      ${CONTAINER_NAME} \
      /bin/bash

  cleanup
