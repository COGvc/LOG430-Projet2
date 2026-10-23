#!/usr/bin/env bash
# Phase 2 : clients B2B de demonstration crees d'avance dans le realm.
set -euo pipefail
set -a; source "$(dirname "$0")/../.env"; set +a
KC=${KC:-http://localhost:8081}; GW=${GW:-http://localhost:${GATEWAY_PORT:-8080}}
TOKEN_URL="$KC/realms/candronex/protocol/openid-connect/token"
jeton() { curl -s -X POST "$TOKEN_URL" -d grant_type=client_credentials -d client_id="$1" -d client_secret="$2" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

echo "1) Inspectra (client de demonstration) obtient un JWT"
TOK=$(jeton inspectra-demo "$INSPECTRA_DEMO_SECRET")
echo "2) Appels via la passerelle"
echo "   POST drone avec jeton      -> $(code -X POST $GW/api/drone-management/v1/drone -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"imsi":"302720000000001"}')  (attendu 201)"
echo "   GET drones sans jeton      -> $(code $GW/api/drone-management/v1/drone)  (attendu 401)"
echo "   GET drones jeton invalide  -> $(code $GW/api/drone-management/v1/drone -H 'Authorization: Bearer abc.def.ghi')  (attendu 401)"
echo "   mauvais secret             -> $(curl -s -X POST "$TOKEN_URL" -d grant_type=client_credentials -d client_id=inspectra-demo -d client_secret=faux | python3 -c 'import sys,json;print(json.load(sys.stdin).get("error"))')  (attendu unauthorized_client)"
