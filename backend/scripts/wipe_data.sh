#!/usr/bin/env bash
# Back up and wipe LexisAI's local-data tree before a schema-breaking change.
# Re-running on an already-empty tree is a no-op (cp + rm both safe).
#
# Usage (from repo root or backend/):
#   bash backend/scripts/wipe_data.sh

set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="$REPO_ROOT/data"

if [ ! -d "$DATA_DIR" ]; then
  echo "no data/ directory at $DATA_DIR — nothing to wipe"
  exit 0
fi

TS="$(date +%s)"
BACKUP="$REPO_ROOT/data.bak.$TS"

echo "backing up $DATA_DIR -> $BACKUP"
cp -R "$DATA_DIR" "$BACKUP"

echo "removing app.db / chroma / uploads / cache"
rm -rf "$DATA_DIR/app.db" "$DATA_DIR/chroma" "$DATA_DIR/uploads" "$DATA_DIR/cache"

echo "done. restart the backend to recreate the schema."
echo "to restore: rm -rf $DATA_DIR && mv $BACKUP $DATA_DIR"
