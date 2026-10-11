#!/usr/bin/env bash
# install-pre-push.sh <repo-path>
# Installs pre-push-check.sh as <repo>/.git/hooks/pre-push.
# Never overwrites a foreign existing hook; re-running is a no-op.
# An older copy of pre-push-check.sh (recognised by its header line) is upgraded.
set -eu

if [ $# -ne 1 ]; then
  echo "usage: $0 <repo-path>" >&2
  exit 2
fi

here="$(cd "$(dirname "$0")" && pwd)"
src="$here/pre-push-check.sh"

if ! git -C "$1" rev-parse --git-dir >/dev/null 2>&1; then
  echo "install-pre-push: $1 is not a git repository" >&2
  exit 1
fi
hook="$(git -C "$1" rev-parse --path-format=absolute --git-path hooks/pre-push)"

if [ -e "$hook" ]; then
  if cmp -s "$src" "$hook"; then
    echo "install-pre-push: already installed ($hook)"
    exit 0
  fi
  if grep -q -m1 '^# pre-push-check.sh - refuse to push a repo that leaks' "$hook" 2>/dev/null; then
    cp "$src" "$hook"
    chmod +x "$hook"
    echo "install-pre-push: upgraded older copy of pre-push-check.sh ($hook)"
    exit 0
  fi
  echo "install-pre-push: a different pre-push hook already exists, not overwriting: $hook" >&2
  exit 1
fi

mkdir -p "$(dirname "$hook")"
cp "$src" "$hook"
chmod +x "$hook"
echo "install-pre-push: installed $hook"
