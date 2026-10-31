#!/usr/bin/env bash
# Tests unitaires (pytest) et règles de dépendance (import-linter) de chaque service, dans un conteneur Python 3.12.
# Aucun service, base ni réseau n'est démarré : les ports sont remplacés par des faux en mémoire.
#   ./scripts/tests-unitaires.sh
set -euo pipefail
cd "$(dirname "$0")/.."
RACINE=$(pwd -W 2>/dev/null || pwd)   # chemin Windows sous Git Bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$RACINE":/srv -w /srv -e PIP_ROOT_USER_ACTION=ignore python:3.12-slim sh -c '
  pip install -q --disable-pip-version-check -r common/requirements.txt pytest==8.* import-linter==2.* &&
  for s in drone-management-service service-ordering-service slice-management-service; do
    echo "== $s"
    (cd services/$s && PYTHONPATH=/srv/common:. python -m pytest -q -p no:cacheprovider tests &&
     PYTHONPATH=/srv/common:. lint-imports --config .importlinter) || exit 1
  done'
