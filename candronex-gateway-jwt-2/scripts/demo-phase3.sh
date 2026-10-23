#!/usr/bin/env bash
# Phase 3 : onboarding par API (UC-01 complet), jeton, appels via la passerelle.
set -euo pipefail
set -a; source "$(dirname "$0")/../.env"; set +a
KC=${KC:-http://localhost:8081}; GW=${GW:-http://localhost:${GATEWAY_PORT:-8080}}
TOKEN_URL="$KC/realms/candronex/protocol/openid-connect/token"
jeton() { curl -s -X POST "$TOKEN_URL" -d grant_type=client_credentials -d client_id="$1" -d client_secret="$2" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

echo "1) L'administrateur obtient un jeton (portee clients:create)"
ADMIN=$(jeton candronex-admin "$CANDRONEX_ADMIN_SECRET")

echo "2) UC-01 : onboarding du client B2B Inspectra"
R=$(curl -s -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d "{\"nom\":\"${NOM:-Inspectra Drone Services}\"}")
echo "   $R" | sed -E 's/"clientSecret":"[^"]+"/"clientSecret":"***"/'
CID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["clientId"])')
SEC=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["clientSecret"])')

echo "3) (hors systeme) L'admin transmet client_id et secret au client B2B par un canal securise"
echo "   Ce script joue les deux roles; en realite, le secret ne transite que par ce canal."
echo "4) Le client B2B echange ses propres client credentials contre son JWT"
TOK=$(jeton "$CID" "$SEC")

echo "5) Appels du client B2B via la passerelle"
echo "   POST drone avec jeton         -> $(code -X POST $GW/api/drone-management/v1/drone -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"imsi":"302720000000001","modele":"M300"}')  (attendu 201)"
echo "   GET drones sans jeton         -> $(code $GW/api/drone-management/v1/drone)  (attendu 401)"
echo "   GET drones jeton invalide     -> $(code $GW/api/drone-management/v1/drone -H 'Authorization: Bearer abc.def.ghi')  (attendu 401)"
echo "   onboarding avec jeton client  -> $(code -X POST $GW/api/client-management/v1/client -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"nom":"Pirate"}')  (attendu 403)"
echo "   X-Client-Id forge par client  -> $(curl -s $GW/api/drone-management/v1/drone -H "Authorization: Bearer $TOK" -H 'X-Client-Id: candronex-admin')"
