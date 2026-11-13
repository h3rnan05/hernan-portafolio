#!/usr/bin/env bash
# Reconciliación por-arranque: levanta PostgreSQL (los servidores web viven en
# `terminals`). Debe tolerar reinicios y devolver control tras confirmar que la
# base está lista.
set -euo pipefail

sudo pg_ctlcluster 16 main start 2>/dev/null || true

for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then
    echo "start.sh: PostgreSQL listo"
    exit 0
  fi
  sleep 1
done

echo "start.sh: PostgreSQL no llegó a estar listo" >&2
exit 1
