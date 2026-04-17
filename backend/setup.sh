#!/usr/bin/env bash
set -euxo pipefail

dotenv_get() {
  local key="$1"
  local value=""
  if [ -f .env ]; then
    value="$(grep -E "^${key}=" .env | tail -n 1 | cut -d= -f2- || true)"
    value="${value%$'\r'}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "$value"
}

LEAN_SERVER_LEAN_VERSION="${LEAN_SERVER_LEAN_VERSION:-$(dotenv_get LEAN_SERVER_LEAN_VERSION)}"
LEAN_SERVER_LEAN_VERSION="${LEAN_SERVER_LEAN_VERSION:-v4.9.0}"
REPL_REPO_URL="${REPL_REPO_URL:-$(dotenv_get REPL_REPO_URL)}"
REPL_REPO_URL="${REPL_REPO_URL:-https://github.com/leanprover-community/repl.git}"
REPL_BRANCH="${REPL_BRANCH:-$(dotenv_get REPL_BRANCH)}"
REPL_BRANCH="${REPL_BRANCH:-$LEAN_SERVER_LEAN_VERSION}"
MATHLIB_REPO_URL="${MATHLIB_REPO_URL:-$(dotenv_get MATHLIB_REPO_URL)}"
MATHLIB_REPO_URL="${MATHLIB_REPO_URL:-https://github.com/leanprover-community/mathlib4.git}"
MATHLIB_BRANCH="${MATHLIB_BRANCH:-$(dotenv_get MATHLIB_BRANCH)}"
MATHLIB_BRANCH="${MATHLIB_BRANCH:-$LEAN_SERVER_LEAN_VERSION}"

command -v curl >/dev/null 2>&1 || { echo >&2 "curl is required"; exit 1; }
command -v git  >/dev/null 2>&1 || { echo >&2 "git is required";  exit 1; }

echo "Installing Elan"
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
  | sh -s -- --default-toolchain "${LEAN_SERVER_LEAN_VERSION}" -y
source "$HOME/.elan/env"

echo "Installing Lean ${LEAN_SERVER_LEAN_VERSION}"
lean --version
echo "Installing REPL from ${REPL_REPO_URL}@${REPL_BRANCH}"
echo "Installing Mathlib from ${MATHLIB_REPO_URL}@${MATHLIB_BRANCH}"

# Version comparison function - only proceeds if args are in vX.Y.Z format.
version_lte() {
  local ver1="$1"
  local ver2="$2"
  
  # Check if both versions match pattern vX.Y.Z
  if ! [[ "$ver1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || ! [[ "$ver2" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    return 1  # Return false if either version doesn't match pattern
  fi
  
  # Strip 'v' prefix and compare versions.
  local v1="${ver1#v}"
  local v2="${ver2#v}"
  printf '%s\n%s\n' "$v1" "$v2" | sort -V -C
}

install_repo() {
  local name="$1" url="$2" branch="$3" upd_manifest="$4"
  echo "Installing ${name}@${branch}..."
  if [ ! -d "$name" ]; then
    git clone --branch "${branch}" --single-branch --depth 1 "$url" "$name"
  fi
  pushd "$name"
    git remote set-url origin "$url"
    if ! git rev-parse --verify --quiet "${branch}^{commit}" >/dev/null; then
      git fetch --depth 1 origin "refs/tags/${branch}:refs/tags/${branch}" \
        || git fetch --depth 1 origin "${branch}:${branch}" \
        || git fetch --depth 1 origin "${branch}"
    fi
    if git show-ref --verify --quiet "refs/tags/${branch}"; then
      git checkout --force --detach "refs/tags/${branch}"
    else
      git checkout --force "${branch}"
    fi
    if [ "$name" = "mathlib4" ]; then
      lake exe cache get
    fi
    lake build
    if [ "$upd_manifest" = "true" ]; then
      jq '.packages |= map(.type="path"|del(.url)|.dir=".lake/packages/"+.name)' \
         lake-manifest.json > lake-manifest.json.tmp && mv lake-manifest.json.tmp lake-manifest.json
    fi
  popd
}

install_repo repl "$REPL_REPO_URL" "$REPL_BRANCH" false

install_repo mathlib4 "$MATHLIB_REPO_URL" "$MATHLIB_BRANCH" true
