#!/usr/bin/env bash
# Cas attendus et scénario de bout en bout, par la passerelle.
# Adapté de scripts/tester-cas-attendus.sh du projet de démarrage. Sort en erreur si un cas échoue.
#   ./scripts/e2e.sh            (système démarré avec docker compose up)
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
PY=${PY:-$(command -v python3 || command -v python)}
KC=${KC:-http://localhost:8081}; GW=${GW:-http://localhost:${GATEWAY_PORT:-8080}}
TOKEN_URL="$KC/realms/candronex/protocol/openid-connect/token"
JSON='Content-Type: application/json'
DRONES=/api/drone-management/v1/drone
RUN=$(date +%s)   # suffixe unique : le script peut être relancé sans conflit

jeton() { curl -s -X POST "$TOKEN_URL" -d grant_type=client_credentials -d client_id="$1" -d client_secret="$2" | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
champ() { "$PY" -c "import sys,json;print(json.load(sys.stdin)$1)"; }
# IMSI/ICCID uniques et valides (PLMN de test 999-70)
imsi() { printf "99970%010d" $(( (RUN * 100 + $1) % 10000000000 )); }
iccid() { printf "89302720%011d" $(( (RUN * 100 + $1) % 100000000000 )); }
modifie() { "$PY" - "$1" <<'PY'
import sys, json, base64
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
h, p, s = sys.argv[1].split(".")
c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
c["azp"] = "aerotech-demo"
print(f"{h}.{b64(json.dumps(c).encode())}.{s}")
PY
}

ECHECS=0
verifier() {  # verifier <libelle> <attendu> <obtenu>
  if [[ "$2" == "$3" ]]; then printf "  OK     %-58s %s\n" "$1" "$3"
  else printf "  ECHEC  %-58s obtenu %s, attendu %s\n" "$1" "$3" "$2"; ECHECS=$((ECHECS+1)); fi
}

echo "== Jetons"
INSP=$(jeton inspectra-demo "$INSPECTRA_DEMO_SECRET")
AERO=$(jeton aerotech-demo "$AEROTECH_DEMO_SECRET")
verifier "jetons obtenus" 2 "$(for j in "$INSP" "$AERO"; do [[ -n "$j" ]] && echo x; done | wc -l | tr -d ' ')"

echo "== UC-02 : drones"
D1="DRN-$RUN-1"
corps() { echo "{\"droneId\":\"$1\",\"imsi\":\"$2\",\"iccid\":\"$3\",\"simType\":\"ESIM\"}"; }
R=$(curl -s -w '\n%{http_code}' -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(corps "$D1" "$(imsi 1)" "$(iccid 1)")")
verifier "enregistrer un drone" 201 "$(echo "$R" | tail -1)"
verifier "réponse sans IMSI ni ICCID" 0 "$(echo "$R" | head -1 | grep -c -i 'imsi\|iccid')"
verifier "même droneId : refusé" 409 "$(code -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(corps "$D1" "$(imsi 2)" "$(iccid 2)")")"
verifier "même IMSI, autre drone : refusé" 409 "$(code -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(corps "$D1-b" "$(imsi 1)" "$(iccid 3)")")"
verifier "IMSI invalide" 400 "$(code -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(corps "$D1-c" 123 "$(iccid 4)")")"
verifier "format d'erreur uniforme" "DONNEES_INVALIDES" "$(curl -s -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(corps "$D1-c" 123 "$(iccid 4)")" | champ '["erreur"]')"
verifier "consulter son drone" 200 "$(code "$GW$DRONES/$D1" -H "Authorization: Bearer $INSP")"
verifier "lister ses drones" 200 "$(code "$GW$DRONES?limit=5" -H "Authorization: Bearer $INSP")"

echo "== Refus"
verifier "sans jeton" 401 "$(code "$GW$DRONES")"
verifier "jeton modifié (azp changé, signature d'origine)" 401 "$(code "$GW$DRONES" -H "Authorization: Bearer $(modifie "$INSP")")"
verifier "jeton illisible" 401 "$(code "$GW$DRONES" -H 'Authorization: Bearer abc.def.ghi')"
verifier "drone d'Inspectra demandé par Aerotech" 404 "$(code "$GW$DRONES/$D1" -H "Authorization: Bearer $AERO")"
verifier "X-Client-Id forgé par Aerotech : aucun drone d'Inspectra" 0 \
  "$(curl -s "$GW$DRONES" -H "Authorization: Bearer $AERO" -H 'X-Client-Id: inspectra-demo' | grep -c "$D1")"
verifier "route interne non exposée par la passerelle" 404 "$(code "$GW/internal/v1/drone/$D1/network-identity?clientId=inspectra-demo")"
verifier "service non publié sur l'hôte" 000 "$(code --max-time 2 http://localhost:8000/health)"

echo "== Corrélation"
cid() { curl -s -D - -o /dev/null "$@" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-correlation-id"{print $2}'; }
verifier "X-Correlation-Id fourni : renvoyé" "e2e-$RUN" "$(cid "$GW$DRONES/$D1" -H "Authorization: Bearer $INSP" -H "X-Correlation-Id: e2e-$RUN")"
verifier "X-Correlation-Id absent : généré (UUID)" 36 "$(cid "$GW$DRONES/$D1" -H "Authorization: Bearer $INSP" | tr -d '\n' | wc -c | tr -d ' ')"
curl -s -o /dev/null -X POST "$GW$DRONES" -H "Authorization: Bearer $INSP" -H "$JSON" -H "X-Correlation-Id: e2e-$RUN" \
  -d "$(corps "$D1-cid" "$(imsi 5)" "$(iccid 5)")"
verifier "X-Correlation-Id dans le journal du service" 1 \
  "$(docker compose logs drone-management-service 2>/dev/null | grep -c "\"correlationId\": \"e2e-$RUN\"" | tr -d ' ' | sed 's/^[1-9][0-9]*$/1/')"
verifier "aucun IMSI complet dans les journaux" 0 "$(docker compose logs drone-management-service 2>/dev/null | grep -c "$(imsi 1)")"

echo "== UC-04 : commande"
CMD=/api/service-ordering/v1/serviceOrder
commande() { echo "{\"items\":[{\"droneId\":\"$1\",\"serviceType\":\"C2\"},{\"droneId\":\"$1\",\"serviceType\":\"IMAGERY\"}]}"; }
verifier "catalogue (C2, IMAGERY)" "C2,IMAGERY" "$(curl -s "$GW/api/service-ordering/v1/catalog" -H "Authorization: Bearer $INSP" | champ '' | "$PY" -c "import sys,ast;print(','.join(s['serviceType'] for s in ast.literal_eval(sys.stdin.read())))")"
verifier "commande sans Idempotency-Key" 400 "$(code -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -d "$(commande "$D1")")"
R=$(curl -s -w '\n%{http_code}' -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -H "Idempotency-Key: k-$RUN" -d "$(commande "$D1")")
verifier "commande C2 + IMAGERY" 201 "$(echo "$R" | tail -1)"
ORDER=$(echo "$R" | head -1 | champ '["orderId"]')
R2=$(curl -s -w '\n%{http_code}' -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -H "Idempotency-Key: k-$RUN" -d "$(commande "$D1")")
verifier "même clé, même corps : même commande, sans doublon" "200 $ORDER" "$(echo "$R2" | tail -1) $(echo "$R2" | head -1 | champ '["orderId"]')"
verifier "même clé, autre corps" 422 "$(code -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -H "Idempotency-Key: k-$RUN" -d "$(commande "$D1-cid")")"
verifier "drone d'un autre client" 422 "$(code -X POST "$GW$CMD" -H "Authorization: Bearer $AERO" -H "$JSON" -H "Idempotency-Key: k-$RUN" -d "$(commande "$D1")")"
verifier "service hors catalogue" 422 "$(code -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -H "Idempotency-Key: k2-$RUN" -d "{\"items\":[{\"droneId\":\"$D1\",\"serviceType\":\"VIDEO\"}]}")"
verifier "consulter sa commande" 200 "$(code "$GW$CMD/$ORDER" -H "Authorization: Bearer $INSP")"
verifier "commande d'Inspectra demandée par Aerotech" 404 "$(code "$GW$CMD/$ORDER" -H "Authorization: Bearer $AERO")"

echo "== UC-06 : activation"
attendre_etat() {  # attendre_etat <orderId> <état global attendu> : 15 s au plus
  local e=""
  for _ in $(seq 1 30); do
    e=$(curl -s "$GW$CMD/$1" -H "Authorization: Bearer $INSP" | champ '["state"]')
    [[ "$e" == "$2" ]] && break; sleep 0.5
  done
  echo "$e"
}
verifier "commande C2 + IMAGERY : les deux services actifs" COMPLETED "$(attendre_etat "$ORDER" COMPLETED)"
SVC="$GW/api/slice-management/v1/service"
verifier "un seul service par élément malgré le rejeu" 2 "$(curl -s "$SVC?orderId=$ORDER" -H "Authorization: Bearer $INSP" | champ '.__len__()')"
verifier "inventaire du drone" "ACTIVE,ACTIVE" "$(curl -s "$SVC?droneId=$D1" -H "Authorization: Bearer $INSP" | "$PY" -c "import sys,json;print(','.join(s['state'] for s in json.load(sys.stdin)))")"
verifier "inventaire d'Inspectra demandé par Aerotech" 0 "$(curl -s "$SVC?droneId=$D1" -H "Authorization: Bearer $AERO" | champ '.__len__()')"
verifier "aucun IMSI complet dans les journaux de l'activation" 0 "$(docker compose logs slice-management-service 2>/dev/null | grep -c "$(imsi 1)")"

if [[ "${TESTER_ECHEC:-1}" == 1 ]]; then
  echo "== UC-06 : activation en échec (taux d'échec forcé à 100 %)"
  MOCK_TAUX_ECHEC=1 docker compose up -d --wait slice-management-service >/dev/null 2>&1
  R=$(curl -s -X POST "$GW$CMD" -H "Authorization: Bearer $INSP" -H "$JSON" -H "Idempotency-Key: echec-$RUN" -d "$(commande "$D1-cid")")
  ORDER_ECHEC=$(echo "$R" | champ '["orderId"]')
  verifier "commande en échec : état FAILED" FAILED "$(attendre_etat "$ORDER_ECHEC" FAILED)"
  verifier "cause NETWORK_REJECTED" "NETWORK_REJECTED" "$(curl -s "$GW$CMD/$ORDER_ECHEC" -H "Authorization: Bearer $INSP" | champ '["items"][0]["failureCause"]')"
  docker compose up -d --wait slice-management-service >/dev/null 2>&1
fi

if [[ "${SAUTER_JETON_COURT:-0}" != 1 ]]; then
  echo "== Expiration (jeton de 5 s)"
  COURT=$(jeton jeton-court "$JETON_COURT_SECRET") # demandé juste avant : il expire 5 s plus tard
  verifier "jeton court encore valide" 200 "$(code "$GW$DRONES?limit=1" -H "Authorization: Bearer $COURT")"
  sleep 6
  verifier "jeton expiré" 401 "$(code "$GW$DRONES?limit=1" -H "Authorization: Bearer $COURT")"
fi

echo
if (( ECHECS == 0 )); then echo "Tous les cas sont respectés."; else echo "$ECHECS cas en échec."; exit 1; fi
