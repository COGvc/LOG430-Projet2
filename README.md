# CanDroneX — Phase 2 (microservices)

Plateforme B2B de connectivité 5G pour drones : un client enregistre ses drones, commande des services
réseau (C2, IMAGERY) et suit leur activation. La Phase 2 découpe le système en trois microservices
derrière une passerelle, avec une base de données par service.

- **api-gateway** (KrakenD, `:8080`) : point d'entrée unique, valide le JWT et route vers les services.
- **keycloak** (`:8081`) : émet les jetons (OAuth2 client credentials, realm `candronex`).
- **drone-management-service** : drones et identité réseau (IMSI, ICCID).
- **service-ordering-service** : catalogue et commandes; transmet les éléments commandés à l'activation.
- **slice-management-service** : activation des services sur le réseau (`MockOpen5GSAdapter`) et inventaire.
- **drone-db**, **ordering-db**, **slice-db** (PostgreSQL 16) : chacune sur un réseau privé, joignable par son seul service.

## Prérequis

- Docker Desktop (avec Docker Compose).
- bash pour les scripts (Git Bash sous Windows), curl et Python 3.

## Démarrage

```bash
cp .env.example .env
```

Remplir chaque valeur vide de `.env` (secrets des clients, mots de passe des bases). Keycloak injecte les
secrets des clients dans le realm à son démarrage; `.env` n'est jamais versionné.

```bash
docker compose up -d --build
```

Tous les conteneurs doivent être `healthy` (environ 1 min, Keycloak est le plus lent) :

```bash
docker compose ps
```

## Obtenir un jeton et appeler l'API

Le contrat complet est dans [openapi/candronex-gateway.yaml](openapi/candronex-gateway.yaml).
Les commandes suivantes déroulent le scénario de base pour le client `inspectra-demo`.

```bash
set -a; source .env; set +a
TOKEN=$(curl -s http://localhost:8081/realms/candronex/protocol/openid-connect/token \
  -d grant_type=client_credentials -d client_id=inspectra-demo -d client_secret="$INSPECTRA_DEMO_SECRET" \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

1. Enregistrer un drone :

```bash
curl -s -X POST http://localhost:8080/api/drone-management/v1/drone -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"droneId":"DRN-0231","imsi":"999700000000231","iccid":"8930272000000000231","simType":"ESIM"}'
```

2. Commander C2 et IMAGERY (la même `Idempotency-Key` rejoue la commande sans la dupliquer) :

```bash
curl -s -X POST http://localhost:8080/api/service-ordering/v1/serviceOrder -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-0001' -d '{"items":[{"droneId":"DRN-0231","serviceType":"C2"},{"droneId":"DRN-0231","serviceType":"IMAGERY"}]}'
```

3. Suivre la commande (remplacer `SO-1001` par l'`orderId` reçu); elle passe à `COMPLETED` en 1 à 2 s :

```bash
curl -s http://localhost:8080/api/service-ordering/v1/serviceOrder/SO-1001 -H "Authorization: Bearer $TOKEN"
```

4. Consulter l'inventaire des services du drone :

```bash
curl -s "http://localhost:8080/api/slice-management/v1/service?droneId=DRN-0231" -H "Authorization: Bearer $TOKEN"
```

Le jeton expire après 300 s : en redemander un au besoin. Le client `aerotech-demo` (secret
`AEROTECH_DEMO_SECRET`) ne voit rien des drones ni des commandes d'`inspectra-demo`.

## Tests

Tests unitaires et contrats d'architecture hexagonale (pytest et import-linter, dans un conteneur Python) :

```bash
bash scripts/tests-unitaires.sh
```

Cas attendus de bout en bout, par la passerelle (système démarré) :

```bash
bash scripts/e2e.sh
```

- `SAUTER_JETON_COURT=1` saute le cas du jeton expiré (attente de 5 s).
- `TESTER_ECHEC=0` saute le cas d'activation en échec, qui redémarre slice-management-service avec un taux d'échec de 100 %.

## Mesure de charge

Test k6 par la passerelle (1 min de montée, puis 5 min à 600 ops/s; 60 % consultations, 30 % commandes,
10 % enregistrements), suivi des files d'attente et P95 du délai d'activation calculé dans les bases :

```bash
bash load/mesurer.sh
```

Essai court : `DEBIT=50 PLATEAU=20s bash load/mesurer.sh`. Les résultats vont dans `load/resultats/<nom>.{txt,json,csv}`
(`NOM=...` pour choisir le nom). Le script de charge est [load/charge.js](load/charge.js) et la requête du P95
[load/p95.sql](load/p95.sql) : un service pas encore actif à la fin du test compte comme dépassant 5 s.

Dernier résultat (12 processeurs, 16 Go) : 0 % d'erreur, 0 itération abandonnée, P95 d'activation 1,46 s.

## Configuration

Variables lues depuis `.env` ou l'environnement, avec leur valeur par défaut :

| Variable | Défaut | Effet |
|---|---|---|
| `GATEWAY_PORT` | 8080 | Port publié de la passerelle |
| `DRONE_WORKERS`, `ORDERING_WORKERS`, `SLICE_WORKERS` | 2, 4, 4 | Processus uvicorn par service |
| `MOCK_LATENCE_MIN_MS`, `MOCK_LATENCE_MAX_MS` | 300, 1500 | Latence simulée du réseau par activation |
| `MOCK_TAUX_ECHEC` | 0 | Proportion d'activations refusées par le réseau simulé (0 à 1) |
| `ACTIVATION_CONCURRENCE` | 500 | Activations simultanées par processus de slice-management-service |

## Arborescence

- `common/` : bibliothèque partagée (`candronex_common`) : journaux JSON, corrélation, erreurs, validation du jeton, client HTTP et disjoncteur.
- `services/<service>/` : code (`app/`, architecture hexagonale), tests, contrats import-linter et Dockerfile de chaque service.
- `krakend/` : configuration de la passerelle, générée par `generer.py`.
- `keycloak/` : realm `candronex` importé au démarrage.
- `openapi/` : contrat de l'API publique.
- `load/` : test de charge et protocole de mesure.
- `scripts/` : tests unitaires et de bout en bout.
- `notes/` : décisions d'implémentation.
- `candronex-gateway-jwt-2/` : projet de démarrage fourni, gardé comme référence.
