# CanDroneX – Projet de démarrage : API Gateway + OAuth2 / JWT

LOG430 – Architecture logicielle – Phase 2. Ce squelette montre, de bout en bout, comment :

- exposer les microservices **uniquement** par une API Gateway (KrakenD, comme au Labo 05);
- authentifier les clients B2B par le flux **OAuth2 client credentials** (Keycloak);
- propager un **identifiant de corrélation** (`X-Correlation-Id`) de la passerelle jusqu'aux journaux des microservices;
- faire valider les **jetons JWT** par la passerelle **et** par chaque microservice (signature, expiration, émetteur, **audience `candronex-api`**);
- réaliser **UC-01** en deux temps :
  - **Phase 2** : des clients B2B de démonstration (`inspectra-demo`, `aerotech-demo`) sont créés d'avance dans le realm; ils obtiennent un JWT validé par la passerelle;
  - **Phase 3** : l'onboarding par API (`client-management-service`), où un administrateur crée de nouveaux clients B2B.

Il ne s'agit pas d'une solution du projet : remplacez `drone-management-service` par vos propres microservices.

## Architecture

```
                         (1) client_id + secret
  Client B2B  ─────────────────────────────────────►  Keycloak :8081   (serveur d'autorisation)
      │         ◄─── JWT (azp = client_id, aud = candronex-api) ──
      │
      │ (2) Authorization: Bearer <JWT>
      ▼
  API Gateway :8080 (KrakenD)
   - valide signature (JWKS), expiration, émetteur, audience
   - vérifie la portée clients:create pour l'onboarding
   - injecte X-Client-Id = claim azp, transmet Authorization
   - transmet X-Correlation-Id (le service le génère s'il est absent)
      │                                      │
      ▼                                      ▼
  client-management-service (UC-01)     drone-management-service (exemple)
   revalide le JWT (aud, portée)         revalide le JWT (aud)
   └─► API d'admin Keycloak              filtre les données par azp
       (crée le client + secret)
```

Seuls la passerelle (8080) et Keycloak (8081) sont exposés. Les microservices n'ont pas de section `ports:`.

## Convention de nommage

Chaque API a sa propre version : `/api/{api}/v{n}/{ressource}` (ressource au singulier, comme les API TM Forum). Le microservice porte le nom de l'API suivi de `-service`; c'est aussi son nom DNS sur le réseau Docker.

| Route publique (passerelle) | Microservice | Hôte interne |
|---|---|---|
| `POST/GET /api/drone-management/v1/drone`, `GET /api/drone-management/v1/drone/{droneId}` | `drone-management-service` | `http://drone-management-service:5002` |
| `POST /api/client-management/v1/client` (portée `clients:create`) | `client-management-service` | `http://client-management-service:5001` |

Un microservice purement interne (appelé seulement par d'autres services) suit la même convention, mais n'a **aucune** route dans `krakend.json`. Les appels entre services passent par le réseau Docker (pas par la passerelle) et transmettent `Authorization` et `X-Correlation-Id`.

## Démarrage

```bash
cp .env.example .env        # puis changez les secrets
docker compose up --build
./scripts/demo-phase2.sh    # Phase 2 : clients de démonstration
./scripts/demo-phase3.sh    # Phase 3 : onboarding par API (UC-01 complet)
```

Sortie attendue du scénario Phase 3 :

```
POST drone avec jeton         -> 201
GET drones sans jeton         -> 401
GET drones jeton invalide     -> 401
onboarding avec jeton client  -> 403   (portée clients:create absente)
X-Client-Id forgé par client  -> la liste du client authentifié, pas celle de l'identité forgée
```

## Les pièces

| Fichier | Rôle |
|---|---|
| `keycloak/realm-candronex.json` | Realm importé au démarrage : portée `candronex-api-audience` (mapper d'audience qui ajoute `candronex-api` à `aud`), attachée aux clients de l'API et, par `defaultDefaultClientScopes`, à tout client créé ensuite (onboarding de la Phase 3); clients de démonstration `inspectra-demo` et `aerotech-demo` (Phase 2); portée `clients:create`, client `candronex-admin` et compte technique `client-management-service` (Phase 3). Les secrets viennent du `.env` (`${...}`). |
| `krakend/krakend.json` | Routes de la passerelle. Chaque route porte un `auth/validator` (JWKS de Keycloak, émetteur, portées, `audience: ["candronex-api"]`) et `propagate_claims` qui copie `azp` dans `X-Client-Id`. `Authorization` est transmis aux services pour qu'ils revalident le jeton. |
| `client-management-service/` | UC-01 complet (Phase 3). Domaine (`ClientB2B`, service `OnboarderClientB2B`), port `FournisseurIdentitePort`, adaptateur `KeycloakAdapter`. Le secret est renvoyé une seule fois et n'est jamais journalisé. |
| `*/app/correlation.py` | Reprend `X-Correlation-Id` transmis par la passerelle (ou en génère un), l'ajoute à chaque ligne de journal et le renvoie dans la réponse. |
| `*/app/jeton.py` | Vérification du JWT dans chaque microservice (PyJWT + JWKS de Keycloak) : signature RS256, `exp`, `iss`, `aud` contient `candronex-api`, `azp` présent. Configurable par `JWT_AUDIENCE`, `JWT_ISSUER`, `JWT_JWKS_URL`. |
| `drone-management-service/` | Exemple de microservice protégé : rattache chaque drone au client et répond 404 pour le drone d'un autre client. |
| `scripts/demo-phase2.sh` | Jeton d'un client de démonstration → appels, avec les cas d'erreur. |
| `scripts/demo-phase3.sh` | Onboarding → jeton → appels, avec les cas d'erreur. |

## Correspondance avec les diapositives

| Diapositive | Où c'est dans le code |
|---|---|
| 3-4 · point d'entrée unique, services internes | `docker-compose.yml` : seuls `api-gateway` et `keycloak` ont des `ports:` |
| 5-6 · flux client credentials, jeton de 5 min | `keycloak/realm-candronex.json` (`accessTokenLifespan: 300`, `serviceAccountsEnabled`) |
| 9 · anatomie du JWT (`iss`, `exp`, `azp`, `scope`) | affiché, décodé, au début de `scripts/tester-cas-attendus.sh` |
| 10 · signature, expiration, émetteur, portées | `krakend/krakend.json` : `auth/validator` (`jwk_url`, `issuer`, `scopes`) |
| Plan · UC-01 onboarding d'un client B2B | `client-management-service/` (domaine, port, `KeycloakAdapter`) |
| 15 · cas attendus | `scripts/tester-cas-attendus.sh` |
| 15 · k6 avec `setup()` | `load-tests/drones.js` |

## Tester

```bash
./scripts/tester-cas-attendus.sh                           # les cas de la slide 15, de bout en bout
(cd client-management-service && python -m pytest tests)   # domaine UC-01, sans Keycloak
(cd drone-management-service && python -m pytest tests)    # vérification du JWT (audience, etc.), sans Keycloak
k6 run -e CLIENT_SECRET=$INSPECTRA_DEMO_SECRET load-tests/drones.js
```

| Cas | Attendu | Vérifié par |
|---|---|---|
| Jeton valide | 200 / 201 | Passerelle |
| Sans jeton | 401 | Passerelle |
| Jeton expiré, modifié, non signé (`alg: none`), autre émetteur | 401 | Passerelle (et microservice) |
| Jeton sans l'audience `candronex-api` (ex. compte `client-management-service`) | 401 | Passerelle (et microservice) |
| `X-Correlation-Id` fourni / absent | renvoyé tel quel / généré, présent dans les journaux | Passerelle et microservice |
| Service arrêté ou injoignable | 500 sans corps (limite de KrakenD CE, voir « Pièges connus ») | Passerelle |
| Onboarding sans la portée `clients:create` | 403 | Passerelle |
| Drone d'un autre client | 404 | drone-management-service |
| Client déjà existant (dans le dépôt ou déjà dans Keycloak) | 409 | client-management-service |

Le cas « jeton expiré » utilise le client `jeton-court` (tests seulement), dont le jeton dure 5 s. Si le port 8080 est occupé, mettez `GATEWAY_PORT=8088` dans `.env`; les scripts le lisent.

## Obtenir un jeton à la main

```bash
curl -X POST http://localhost:8081/realms/candronex/protocol/openid-connect/token \
  -d grant_type=client_credentials -d client_id=<clientId> -d client_secret=<secret>
```

Aucun utilisateur n'intervient : le jeton représente le client B2B lui-même, pas une personne.

## Pièges connus

- **CORS** : tout en-tête envoyé par un navigateur doit figurer dans `allow_headers` (ici `X-Correlation-Id`), sinon la requête préalable (preflight) échoue.
- **Service indisponible** : avec `output_encoding: no-op`, KrakenD CE répond **500 sans corps** quand un microservice est arrêté ou injoignable, et ses propres 401/403 n'ont pas de corps non plus. Ni Lua (`post` ne s'exécute pas sur erreur) ni la configuration ne permettent d'y mettre un corps JSON sans plugin Go. Les erreurs produites par les microservices, elles, ont le format uniforme `{"erreur", "message"}`.
- **Audience (`aud`)** : Keycloak n'ajoute pas d'audience « API » par défaut; c'est le mapper `oidc-audience-mapper` de la portée `candronex-api-audience` qui l'ajoute. Un client sans cette portée (ici `client-management-service`) obtient un jeton valide mais refusé par l'API (401). L'identité du client reste la claim `azp` (et non `sub`, qui est l'identifiant du compte de service).
- **Émetteur (`iss`)** : `KC_HOSTNAME=http://localhost:8081` fixe l'émetteur, que le jeton soit demandé via `localhost:8081` ou `keycloak:8080`. Sans cela, la passerelle rejette les jetons.
- **En-têtes** : KrakenD ne transmet que les en-têtes listés dans `input_headers`. `X-Client-Id` y figure pour que la valeur injectée par `propagate_claims` atteigne les services; une valeur envoyée par le client est écrasée.
- **`disable_jwk_security: true`** n'est acceptable que parce que Keycloak est joint en HTTP sur le réseau Docker interne.
- **Tests de charge** : obtenez le jeton une fois (`setup()` dans k6), pas à chaque requête.

## Versions validées

Keycloak 26.0, KrakenD CE 2.7.2, Python 3.12 / Flask 3.1, k6. `tester-cas-attendus.sh`, `demo-phase2.sh`, `demo-phase3.sh` et `load-tests/drones.js` passent avec `docker compose up --build` (Docker Desktop, arm64).
