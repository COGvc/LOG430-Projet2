#!/usr/bin/env bash
# Protocole de mesure : test de charge k6 + suivi des services en attente + P95 du délai d'activation.
#   ./load/mesurer.sh                       (énoncé : 1 min de montée, 5 min à 600 ops/s)
#   DEBIT=50 PLATEAU=20s ./load/mesurer.sh  (essai court)
# Résultats dans load/resultats/<nom>.{json,csv,txt}. Le système doit être démarré (docker compose up).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
NOM=${NOM:-mesure-$(date -u +%Y%m%dT%H%M%SZ)}
RES=load/resultats; mkdir -p "$RES"
RACINE=$(pwd -W 2>/dev/null || pwd)
RESEAU=$(docker network ls --format '{{.Name}}' | grep -E '_backend$' | head -1)
psql() { docker compose exec -T "$1" psql -U "$2" -d "$2" -At "${@:3}"; }

echo "== Environnement" | tee "$RES/$NOM.txt"
{ docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} processeurs, {{.MemTotal}} octets de mémoire'
  echo "Mock : ${MOCK_LATENCE_MIN_MS:-300}-${MOCK_LATENCE_MAX_MS:-1500} ms, taux d'échec ${MOCK_TAUX_ECHEC:-0}"
  echo "Charge : ${DEBIT:-600} ops/s, montée ${MONTEE:-1m}, plateau ${PLATEAU:-5m}"; } | tee -a "$RES/$NOM.txt"

# Suivi toutes les 10 s : éléments pas encore transmis (ordering) et services pas encore terminés (slice).
echo "instant,elements_non_transmis,services_en_attente,services_en_cours" > "$RES/$NOM.csv"
( while true; do
    a=$(psql ordering-db ordering -c "SELECT count(*) FROM order_item WHERE dispatch_status = 'PENDING'")
    b=$(psql slice-db slice -c "SELECT count(*) FILTER (WHERE state = 'PENDING') || ',' || count(*) FILTER (WHERE state = 'ACTIVATING') FROM service WHERE state IN ('PENDING', 'ACTIVATING')")
    echo "$(date -u +%H:%M:%S),$a,$b" >> "$RES/$NOM.csv"; sleep 10
  done ) &
SUIVI=$!

DEBUT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
MSYS_NO_PATHCONV=1 docker run --rm --network "$RESEAU" -v "$RACINE/load":/scripts -v "$RACINE/$RES":/resultats \
  -e INSPECTRA_DEMO_SECRET -e AEROTECH_DEMO_SECRET -e DEBIT -e MONTEE -e PLATEAU -e NOM="$NOM" \
  grafana/k6:0.54.0 run --quiet /scripts/charge.js 2>&1 | tee -a "$RES/$NOM.txt"
FIN=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
kill $SUIVI 2>/dev/null

TOTAL=$(psql ordering-db ordering -c "SELECT count(*) FROM order_item i JOIN service_order o USING (order_id)
  WHERE o.accepted_at >= '$DEBUT' AND o.accepted_at < '$FIN'")
echo "== Délai d'activation (fenêtre $DEBUT → $FIN)" | tee -a "$RES/$NOM.txt"
docker compose exec -T slice-db psql -U slice -d slice -v debut="$DEBUT" -v fin="$FIN" -v total="$TOTAL" \
  < load/p95.sql | tee -a "$RES/$NOM.txt"
echo "== Services en attente (toutes les 10 s)" | tee -a "$RES/$NOM.txt"
column -t -s, "$RES/$NOM.csv" 2>/dev/null | tee -a "$RES/$NOM.txt" || cat "$RES/$NOM.csv"
