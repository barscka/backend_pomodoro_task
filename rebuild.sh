#!/usr/bin/env bash

set -Eeuo pipefail

echo "Sincronizando o Git..."
git pull --ff-only origin main

echo "Reconstruindo os serviços..."
docker compose down
docker compose up \
  --build \
  --force-recreate \
  --remove-orphans \
  --no-deps \
  -d

echo "Estado dos serviços:"
docker compose ps
