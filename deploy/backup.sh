#!/usr/bin/env sh
# Nightly database dump, keeping 7 days. Cron (as root):
#   15 3 * * * /opt/lead-platform/deploy/backup.sh >> /var/log/lead-platform-backup.log 2>&1
set -eu
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f docker-compose.prod.yml --env-file compose.env exec -T postgres \
  pg_dump -U leads -d leads --format=custom > "backups/leads-$stamp.dump"
find backups -name 'leads-*.dump' -mtime +7 -delete
echo "backup ok: backups/leads-$stamp.dump"
