#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

cd "$PROJECT_DIR"

echo "$LOG_PREFIX Publishing docs to gh-pages..."

TMPDIR="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$TMPDIR" >/dev/null 2>&1 || true
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

git fetch origin gh-pages:gh-pages >/dev/null 2>&1 || true
git worktree add "$TMPDIR" gh-pages >/dev/null

rm -rf "$TMPDIR"/*
cp -r docs/* "$TMPDIR"/

git -C "$TMPDIR" add -A
if git -C "$TMPDIR" diff --cached --quiet; then
  echo "$LOG_PREFIX No site changes to publish."
  exit 0
fi

git -C "$TMPDIR" commit -m "Daily site update: $(date '+%Y-%m-%d')" >/dev/null
git -C "$TMPDIR" push origin gh-pages >/dev/null

echo "$LOG_PREFIX Published docs to gh-pages."
