#!/bin/sh
set -e

# AD-4/AD-7: data/app.db (the SQLite session/case store) is the ONLY real state
# that must survive a restart or redeploy — it lives on the mounted volume at
# /app/data (see fly.toml [[mounts]] / render.yaml disk) and this script never
# touches it. fixture.duckdb, demo_users.json and classifier.joblib are
# read-only build artifacts, not volume state, so they are refreshed from the
# image on EVERY boot: a redeploy with a rebuilt fixture or a retrained
# classifier must actually take effect, instead of a stale first-boot copy on
# the volume winning forever. Each copy is atomic (temp file + rename) so a
# boot killed mid-copy (OOM, full disk) never leaves a truncated file that a
# later boot mistakes for a good one.
mkdir -p /app/data
for f in /app/seed-data/*; do
  name=$(basename "$f")
  if cp "$f" "/app/data/.$name.tmp" && mv "/app/data/.$name.tmp" "/app/data/$name"; then
    echo "entrypoint: seeded /app/data/$name from the image"
  else
    echo "entrypoint: FAILED to seed /app/data/$name (volume full or not writable?)" >&2
    exit 1
  fi
done

exec "$@"
