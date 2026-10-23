#!/bin/sh
# Render the app icon PNG set from the SVG source.
set -e
cd "$(dirname "$0")/../packaging/icons"
for size in 16 24 32 48 64 128 256; do
  # Small sizes use a simplified source (thicker ring, no accent tile) for legibility.
  if [ "$size" -le 24 ]; then
    src=wyze-view-small.svg
    magick -background none -density 384 "$src" -resize "${size}x${size}" \
      -alpha on "wyze-view-${size}.png"
  else
    src=wyze-view.svg
    magick -background none -density 384 "$src" -resize "${size}x${size}" \
      "wyze-view-${size}.png"
  fi
done
echo "rendered icon set"
