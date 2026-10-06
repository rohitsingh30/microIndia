#!/bin/sh
# Refresh the hosted dashboard's data: rebuild the database snapshot, replace the "snapshot-latest"
# release asset, and start the image build. Run from the root of the hosting checkout:
#   sh deploy/publish_snapshot.sh [path/to/microindia.sqlite3]
# Then pin the new image digest in Nikamma (apps/microindia/kustomization.yaml) to roll it out.
set -eu
SOURCE=${1:-/Users/rohit/projects/microIndia/apps/scraper/data/microindia.sqlite3}
python3 deploy/make_snapshot.py --source "$SOURCE" --out deploy/out
gh release view snapshot-latest >/dev/null 2>&1 || \
  gh release create snapshot-latest --title "Database snapshot (latest)" \
    --notes "Read-only data for the hosted dashboard." --target hosting
gh release upload snapshot-latest deploy/out/microindia-snapshot.sqlite3.gz --clobber
gh workflow run hosting-image.yml --ref hosting
echo "Snapshot uploaded; image build started (gh run list --workflow hosting-image.yml)."
