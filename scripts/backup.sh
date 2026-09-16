#!/usr/bin/env sh
set -eu
umask 077
mkdir -p backups
backup_file="backups/imkonex-$(date -u +%Y%m%dT%H%M%SZ).dump"
temp_file="${backup_file}.partial"
trap 'rm -f "$temp_file"' EXIT HUP INT TERM
docker compose exec -T db pg_dump -U imkonex -d imkonex -Fc > "$temp_file"
test -s "$temp_file"
mv "$temp_file" "$backup_file"
echo "Backup created: $backup_file"
