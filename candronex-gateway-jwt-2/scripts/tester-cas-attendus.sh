#!/usr/bin/env bash
# Tester : les cas attendus (slide 15 du cours « API Gateway et securite OAuth2 / JWT »).
# Chaque cas affiche OK/ECHEC; le script sort en erreur si un cas echoue.
set -uo pipefail
cd "$(dirname "$0")/.."   # docker compose (cas « service indisponible ») s execute depuis la racine du projet
set -a; source .env; set +a
KC=${KC:-http://localhost:8081}; GW=${GW:-http://localhost:${GATEWAY_PORT:-8080}}
TOKEN_URL="$KC/realms/candronex/protocol/openid-connect/token"
JSON='Content-Type: application/json'

jeton() { curl -s -X POST "$TOKEN_URL" -d grant_type=client_credentials -d client_id="$1" -d client_secret="$2" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
champ() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
# Contenu (claims) d'un JWT, sans le valider : « decoder n'est pas valider ».
claims() { python3 - "$1" <<'PY'
import sys, json, base64
p = sys.argv[1].split(".")[1]; p += "=" * (-len(p) % 4)
print(json.dumps(json.loads(base64.urlsafe_b64decode(p)), indent=2))
PY
}
# Jeton modifie : on change un claim du contenu et on garde la signature d'origine.
modifie() { python3 - "$1" <<'PY'
import sys, json, base64
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
h, p, s = sys.argv[1].split(".")
c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
c["azp"] = "candronex-admin"; c["scope"] = "clients:create"
print(f"{h}.{b64(json.dumps(c).encode())}.{s}")
PY
}
# Jeton non signe (alg: none) avec le contenu d'un vrai jeton.
non_signe() { python3 - "$1" <<'PY'
import sys, json, base64
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
p = sys.argv[1].split(".")[1]
print(f"{b64(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{p}.")
PY
}

ECHECS=0
verifier() {  # verifier <libelle> <attendu> <obtenu> <verifie par>
  if [[ "$2" == "$3" ]]; then printf "  OK     %-48s %s  (%s)\n" "$1" "$3" "$4"
  else printf "  ECHEC  %-48s obtenu %s, attendu %s  (%s)\n" "$1" "$3" "$2" "$4"; ECHECS=$((ECHECS+1)); fi
}

echo "== Preparation"
ADMIN=$(jeton candronex-admin "$CANDRONEX_ADMIN_SECRET")
INSP=$(jeton inspectra-demo "$INSPECTRA_DEMO_SECRET")
AERO=$(jeton aerotech-demo "$AEROTECH_DEMO_SECRET")
echo "  Contenu decode du jeton administrateur (slide 9) :"
claims "$ADMIN" | python3 -c 'import sys,json;c=json.load(sys.stdin);print("   ",json.dumps({k:c.get(k) for k in ("iss","aud","exp","azp","scope","typ")}))'

echo "== Cas attendus"
# 1. Jeton valide -> 200 / 201
DRONE=$(curl -s -X POST "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP" -H "$JSON" -d '{"imsi":"302720000000231","modele":"M300"}')
DRONE_ID=$(echo "$DRONE" | champ '["droneId"]' 2>/dev/null)
verifier "POST drone avec jeton valide" 201 "$(code -X POST "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP" -H "$JSON" -d '{"imsi":"302720000000232"}')" Passerelle
verifier "GET drones avec jeton valide" 200 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP")" Passerelle

# 2. Sans jeton -> 401
verifier "GET drones sans jeton" 401 "$(code "$GW/api/drone-management/v1/drone")" Passerelle

# 3. Jeton expire, modifie ou non signe -> 401
verifier "jeton modifie (azp/scope changes)" 401 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $(modifie "$INSP")")" Passerelle
verifier "jeton non signe (alg: none)" 401 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $(non_signe "$INSP")")" Passerelle
verifier "jeton illisible" 401 "$(code "$GW/api/drone-management/v1/drone" -H 'Authorization: Bearer abc.def.ghi')" Passerelle
# Emetteur : un jeton authentique, mais du realm master, n'est pas du realm candronex.
MASTER=$(curl -s -X POST "$KC/realms/master/protocol/openid-connect/token" -d grant_type=password -d client_id=admin-cli \
  -d username="$KC_ADMIN" -d password="$KC_ADMIN_PASSWORD" | champ '["access_token"]')
verifier "jeton d'un autre emetteur (realm master)" 401 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $MASTER")" Passerelle
# Audience : un jeton authentique du realm candronex, mais pas destine a l'API (compte client-management-service, sans aud candronex-api).
SANS_AUD=$(jeton client-management-service "$CLIENT_MANAGEMENT_SERVICE_SECRET")
verifier "jeton sans audience candronex-api" 401 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $SANS_AUD")" Passerelle
COURT=$(jeton jeton-court "$JETON_COURT_SECRET")
EXP=$(claims "$COURT" | champ '["exp"]'); ATTENTE=$(( EXP - $(date +%s) + 2 ))
verifier "jeton court encore valide" 200 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $COURT")" Passerelle
echo "  ... attente de l'expiration du jeton court (${ATTENTE}s)"; sleep "$ATTENTE"
verifier "jeton expire (exp depasse)" 401 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $COURT")" Passerelle

# 4. Onboarding sans la portee clients:create -> 403
verifier "onboarding avec jeton client (sans portee)" 403 "$(code -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $INSP" -H "$JSON" -d '{"nom":"Pirate"}')" Passerelle

# 5. Ressource d'un autre client -> 404
verifier "GET drone d'Inspectra par son proprietaire" 200 "$(code "$GW/api/drone-management/v1/drone/$DRONE_ID" -H "Authorization: Bearer $INSP")" Microservice
verifier "GET drone d'Inspectra par Aerotech" 404 "$(code "$GW/api/drone-management/v1/drone/$DRONE_ID" -H "Authorization: Bearer $AERO")" Microservice
FORGE=$(curl -s "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $AERO" -H 'X-Client-Id: inspectra-demo' | champ '.__len__()')
verifier "X-Client-Id forge par Aerotech (nb de drones)" 0 "$FORGE" Passerelle

# 6. Client deja existant -> 409 (UC-01 complet)
NOM="Client Test $(date +%s)"
R=$(curl -s -w '\n%{http_code}' -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $ADMIN" -H "$JSON" -d "{\"nom\":\"$NOM\"}")
verifier "onboarding UC-01 par l'administrateur" 201 "$(echo "$R" | tail -1)" "client-management"
CID=$(echo "$R" | head -1 | champ '["clientId"]'); SEC=$(echo "$R" | head -1 | champ '["clientSecret"]')
NOUVEAU=$(jeton "$CID" "$SEC")
verifier "nouveau client : son jeton passe la passerelle" 200 "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $NOUVEAU")" Passerelle
verifier "client deja existant" 409 "$(code -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $ADMIN" -H "$JSON" -d "{\"nom\":\"$NOM\"}")" "client-management"
verifier "client deja present dans Keycloak (demo)" 409 "$(code -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $ADMIN" -H "$JSON" -d '{"nom":"Inspectra Demo"}')" "client-management"
verifier "nom invalide" 400 "$(code -X POST "$GW/api/client-management/v1/client" -H "Authorization: Bearer $ADMIN" -H "$JSON" -d '{"nom":""}')" "client-management"

echo "== Correlation et service indisponible"
# En-tete de reponse (une seule valeur) : <url> <args curl...>
entete_cid() { curl -s -D - -o /dev/null "$@" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-correlation-id"{print $2}' | paste -sd, -; }
verifier "X-Correlation-Id fourni : transmis et renvoye" "cmd-0042" "$(entete_cid "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP" -H 'X-Correlation-Id: cmd-0042')" "Passerelle + microservice"
GENERE=$(entete_cid "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP")
verifier "X-Correlation-Id absent : genere (UUID)" 36 "${#GENERE}" Microservice
CID_POST="cmd-$(date +%s)"
curl -s -o /dev/null -X POST "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP" -H "$JSON" -H "X-Correlation-Id: $CID_POST" -d '{"imsi":"302720000000555"}'
verifier "X-Correlation-Id dans le journal du microservice" 1 "$(docker compose logs drone-management-service 2>/dev/null | grep -c "\"correlationId\": \"$CID_POST\"")" Microservice
verifier "CORS : X-Correlation-Id autorise" 1 "$(curl -s -D - -o /dev/null -X OPTIONS "$GW/api/drone-management/v1/drone" -H 'Origin: http://exemple.com' \
  -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: authorization,x-correlation-id' | grep -ci '^access-control-allow-headers:.*x-correlation-id')" Passerelle
docker compose stop drone-management-service >/dev/null 2>&1
PANNE=$(curl -s -w '\n%{http_code}' "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP" -H 'X-Correlation-Id: panne-1')
docker compose start drone-management-service >/dev/null 2>&1
for _ in $(seq 1 30); do   # attendre que le service reponde de nouveau (pour les executions suivantes)
  [[ "$(code "$GW/api/drone-management/v1/drone" -H "Authorization: Bearer $INSP")" == 200 ]] && break; sleep 1
done
# KrakenD CE (no-op) repond 500 sans corps quand le service est injoignable (voir README, « Pieges connus »).
verifier "service indisponible : erreur de la passerelle" 500 "$(echo "$PANNE" | tail -1)" Passerelle

echo "== Microservices non exposes"
verifier "drone-management-service hors passerelle (port 5002)" 000 "$(code --max-time 2 http://localhost:5002/health)" "Docker Compose"

echo
if (( ECHECS == 0 )); then echo "Tous les cas attendus sont respectes."; else echo "$ECHECS cas en echec."; exit 1; fi
