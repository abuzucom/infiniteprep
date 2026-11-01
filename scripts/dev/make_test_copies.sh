#!/usr/bin/env bash
# Copy a few music files of each format into a fresh scratch folder for smoke testing.
#
# Usage: scripts/dev/make_test_copies.sh SOURCE_FOLDER [PER_FORMAT]
#   SOURCE_FOLDER  A music folder. The script only reads it and never changes a file there.
#   PER_FORMAT     Copies per format, 3 by default.
#
# The copies land in scratch/test-library/<UTC time>/, which .gitignore excludes. The script
# prints the destination on its last line.
set -u

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
source_dir="${1:-}"
per_format="${2:-3}"
if [ -z "$source_dir" ] || [ ! -d "$source_dir" ]; then
    echo "Pass an existing music folder as the first argument."
    exit 2
fi
case "$per_format" in
    '' | *[!0-9]*)
        echo "PER_FORMAT must be a whole number, for example 3."
        exit 2
        ;;
esac

dest="$repo_root/scratch/test-library/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dest"
copied=0
for ext in mp3 wav aif aiff flac m4a ogg; do
    count=0
    while IFS= read -r -d '' file; do
        [ "$count" -ge "$per_format" ] && break
        cp -p -n -- "$file" "$dest/" && count=$((count + 1))
    done < <(find "$source_dir" -type f -iname "*.$ext" -print0 | sort -z)
    echo "$ext: $count"
    copied=$((copied + count))
done
echo "Copied $copied files to:"
echo "$dest"
