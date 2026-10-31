# Étape 6 — Passerelle et sécurité

Diagrammes : [deploiement.puml](../diagrams/deploiement.puml), [seq-refus.puml](../diagrams/seq-refus.puml),
[seq-uc04-uc06.puml](../diagrams/seq-uc04-uc06.puml).

Alimente le gabarit : §4.4 (vue Déploiement), §8.5 (sécurité), et les ADR de l'étape 7.

Point de départ : le projet de démarrage `candronex-gateway-jwt-2` (KrakenD, Keycloak, `drone-management-service`)
et son script `scripts/tester-cas-attendus.sh`, qui reprend les cas attendus du cours. On **prolonge** ses
conventions plutôt que d'en inventer d'autres.

## 1. Passerelle : KrakenD CE 2.7

| Critère | Décision |
|---|---|
| Produit | KrakenD CE 2.7, comme le projet de démarrage et le Labo 05 (recommandé par l'énoncé). |
| Rôle | Point d'entrée unique : routage, CORS, validation du JWT, transmission de `Authorization`, `X-Correlation-Id` et `Idempotency-Key`. |
| Encodage | `output_encoding: no-op` : le statut et le corps du service passent tels quels; un seul service par route (aucune agrégation). |
| Délai | `timeout: 5s`, le plus long de la chaîne (budget décroissant, étape 4). |
| Liste blanche | Seules les routes déclarées dans `krakend.json` existent; tout le reste répond 404. Les routes `/internal/v1/...` n'y figurent **jamais**. |

Alternative écartée : nginx. Il aurait permis un corps JSON sur les erreurs de la passerelle (502/503/504), mais
n'est pas l'outil du cours, ne valide pas un JWT dans sa version libre et ferait reposer toute l'authentification
sur les services. La limite de KrakenD CE est connue et documentée par le projet de démarrage (§4).

## 2. Routes publiques

Convention du projet de démarrage : `/api/<contexte>/v1/<ressource>`, ressource au singulier.

| Méthode | Route | Service | Cas d'utilisation | En-têtes / paramètres transmis |
|---|---|---|---|---|
| POST | `/api/drone-management/v1/drone` | drone-management-service | UC-02 enregistrer un drone | `Authorization`, `Content-Type`, `X-Correlation-Id` |
| GET | `/api/drone-management/v1/drone` | drone-management-service | lister ses drones | idem |
| GET | `/api/drone-management/v1/drone/{droneId}` | drone-management-service | consulter un drone | idem |
| GET | `/api/service-ordering/v1/catalog` | service-ordering-service | consulter le catalogue | idem |
| POST | `/api/service-ordering/v1/serviceOrder` | service-ordering-service | UC-04 commander | idem + **`Idempotency-Key`** |
| GET | `/api/service-ordering/v1/serviceOrder` | service-ordering-service | lister ses commandes | idem |
| GET | `/api/service-ordering/v1/serviceOrder/{orderId}` | service-ordering-service | suivre une commande (UC-06 vu du client) | idem |
| GET | `/api/slice-management/v1/service` | slice-management-service | inventaire : services d'un drone ou d'une commande | idem + `?droneId=`, `?orderId=` (`input_query_strings`) |

Chaque route porte le même `auth/validator` et `propagate_claims: [["azp", "X-Client-Id"]]` que le projet de
démarrage. Aucune route n'exige de portée (`scopes`) en Phase 2 : tout client B2B authentifié a les mêmes droits
sur **ses** données; la propriété est appliquée par les services.

Routes internes (réseau Docker seulement, jamais dans `krakend.json`) :

| Route | Service | Appel |
|---|---|---|
| `POST /internal/v1/activation` | slice-management-service | D2 (relance, sans jeton) |
| `GET /internal/v1/drone/{droneId}/network-identity?clientId=` | drone-management-service | D4 (activation, sans jeton) |
| `GET /health` | chaque service | sondes Docker Compose |

D1 et D3 utilisent les routes **versionnées** de l'appelé, directement par le réseau Docker, avec le jeton du client
(étape 3).

## 3. JWT : double validation

| Où | Ce qui est vérifié | Pourquoi |
|---|---|---|
| **Passerelle** (`auth/validator`) | Signature RS256 (JWKS de Keycloak, en cache), `exp`, `iss`, `aud = candronex-api`. | Refuser tôt : un appel invalide ne consomme aucune ressource des services, ce qui compte sous 600 ops/s. |
| **Chaque service** (`candronex_common`, étape 5 §7) | Les mêmes vérifications, plus la présence de `azp`. | Exigence de l'énoncé; défense en profondeur si la passerelle est mal configurée ou contournée sur le réseau Docker. |

- **`clientId` = claim `azp` du jeton validé par le service.** `X-Client-Id` (injecté par la passerelle, qui écrase
  une valeur forgée par le client) ne sert qu'aux journaux : la propriété des données ne dépend jamais d'un en-tête.
- La clé publique est téléchargée une fois puis gardée en cache; une clé inconnue (`kid`) provoque un seul
  rechargement. La revalidation coûte donc un calcul de signature, pas un appel réseau.
- **Émetteur** : les jetons sont obtenus par `http://localhost:8081` (hors Docker) mais les JWKS sont lues par
  `http://keycloak:8080` (dans Docker). L'émetteur attendu est donc configuré explicitement
  (`http://localhost:8081/realms/candronex`), comme dans le projet de démarrage.
- Refus : jeton absent, expiré, mal signé, `alg: none`, autre émetteur ou autre audience → **401**. Données d'un
  autre client → **404**, identique à une ressource inexistante (étape 4, seq-refus).

## 4. Format d'erreur uniforme

Les services répondent toujours, pour toute erreur, avec le format du projet de démarrage, complété :

```json
{ "erreur": "DRONE_INTROUVABLE", "message": "Le drone DRN-0231 est introuvable.", "correlationId": "cmd-0042" }
```

| Champ | Contenu |
|---|---|
| `erreur` | Code stable, en majuscules et en français comme dans le projet de démarrage, destiné aux programmes (`NON_AUTHENTIFIE`, `INTROUVABLE`, `DONNEES_INVALIDES`, `DRONE_DEJA_ENREGISTRE`, `DRONE_INTROUVABLE`, `TYPE_SERVICE_INCONNU`, `CLE_IDEMPOTENCE_MANQUANTE`, `CLE_IDEMPOTENCE_REUTILISEE`, `DEPENDANCE_INDISPONIBLE`, `ERREUR_INTERNE`). |
| `message` | Texte lisible, sans donnée sensible (ni IMSI, ni jeton, ni trace de pile). |
| `correlationId` | Le même que l'en-tête `X-Correlation-Id` de la réponse, pour retrouver la requête dans les journaux. |

Le format est produit par un gestionnaire d'exceptions commun (`candronex_common`) : une erreur métier ou technique
non prévue ne peut pas sortir dans un autre format.

**Limites connues de KrakenD CE** (documentées par le projet de démarrage, README « Pièges connus », et vérifiées
par son script de cas attendus) :

| Situation | Réponse | Raison |
|---|---|---|
| Jeton refusé par la passerelle | 401 **sans corps** | `auth/validator` ne produit pas de corps. |
| Service arrêté ou injoignable | 500 **sans corps** | En `no-op`, KrakenD CE n'a ni configuration ni hook Lua exécuté sur erreur pour fabriquer un corps; il faudrait un plugin Go. |

Ce qui reste garanti : toute erreur **produite par un service**, y compris quand c'est une dépendance de ce service
qui est indisponible (503 `DEPENDANCE_INDISPONIBLE` sur D1, étape 4), a le format uniforme. Candidat à un ADR
(étape 7), avec le plugin Go ou un autre produit comme options pour la Phase 3.

## 5. En-têtes et CORS

| En-tête | Traitement |
|---|---|
| `Authorization` | Transmis inchangé jusqu'au service; recopié par D1 et D3. Jamais journalisé. |
| `X-Correlation-Id` | Transmis par la passerelle; généré par le service (UUID) s'il est absent ou mal formé; renvoyé dans la réponse; recopié par **tous** les appels entre services (D1 à D4) et ajouté à chaque ligne de journal. La relance et l'activation, hors requête, n'ont pas d'identifiant reçu : elles envoient l'`orderItemId` (`SOI-1001-1`) comme `X-Correlation-Id`, ce qui garde `grep SO-1001` suffisant pour suivre une commande (étape 4). |
| `Idempotency-Key` | Transmis sur `POST serviceOrder`; obligatoire (400 `CLE_IDEMPOTENCE_MANQUANTE` sinon). |
| `X-Client-Id` | Injecté par la passerelle depuis `azp`; journaux seulement (§3). |

CORS (passerelle) : origines `*`, méthodes `GET`, `POST`, `OPTIONS`; en-têtes permis `Authorization`,
`Content-Type`, `Idempotency-Key`, `X-Correlation-Id`; en-tête exposé `X-Correlation-Id`. L'origine `*` est
acceptable parce que l'API est appelée par des applications (machine à machine), avec un jeton porteur et **sans
cookie** : aucun identifiant n'est envoyé automatiquement par un navigateur. À restreindre si une interface web
cliente apparaît.

## 6. Contrat OpenAPI

- Chaque service publie son contrat FastAPI (`/openapi.json`, non routé par la passerelle) : schémas, codes de
  réponse et format d'erreur.
- Le **contrat de la passerelle**, `openapi/candronex-gateway.yaml`, déclare les routes publiques du §2 et la
  sécurité exigée par l'énoncé :

```yaml
components:
  securitySchemes:
    keycloak:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: http://localhost:8081/realms/candronex/protocol/openid-connect/token
          scopes: {}
security:
  - keycloak: []
```

## 7. Sécurité (§8.5)

| Menace ou exigence | Mesure |
|---|---|
| Appel sans jeton valide | Refus 401 à la passerelle, puis au service (§3). |
| Accès aux données d'un autre client | Filtre `clientId` dans **chaque requête de dépôt**, pas après coup; 404 sans divulgation. |
| `X-Client-Id` forgé | Écrasé par la passerelle, et jamais utilisé pour autoriser (§3). |
| Contournement de la passerelle | Aucun port des services ni des bases n'est publié sur l'hôte (§8); les services revalident le jeton. |
| Accès d'un service à la base d'un autre | Chaque base est sur un réseau Docker privé partagé avec son seul service (§8). |
| Fuite de l'IMSI | Jamais dans une API publique; objet valeur masqué dans les journaux; une seule sortie, D4 (étapes 3 et 5). |
| Secrets dans le dépôt | Secrets des clients et mots de passe des bases dans `.env`, ignoré par Git; un `.env.example` sans valeurs est fourni. Les secrets du realm Keycloak fourni ne sont pas sous notre contrôle (hors périmètre). |
| Jeton ou secret dans les journaux | Journalisation structurée qui n'écrit jamais `Authorization` ni le corps d'une requête de jeton; un refus journalise le **motif**, pas le jeton. |
| Hypothèse assumée | Réseau Docker de confiance (H3) : pas de TLS ni d'authentification entre services; D2 et D4 se fient au `clientId` reçu. Remis en question en Phase 3 (mTLS ou jeton de service). |

## 8. Vue Déploiement

Un seul hôte, `docker compose up` ([deploiement.puml](../diagrams/deploiement.puml)).

| Conteneur | Port publié sur l'hôte | Réseaux |
|---|---|---|
| `api-gateway` (KrakenD) | **8080** | `backend` |
| `keycloak` (fourni) | **8081** (→ 8080) | `backend` |
| `drone-management-service` | aucun | `backend`, `drone-data` |
| `service-ordering-service` | aucun | `backend`, `ordering-data` |
| `slice-management-service` (avec le `MockOpen5GSAdapter`, interne) | aucun | `backend`, `slice-data` |
| `drone-db`, `ordering-db`, `slice-db` (PostgreSQL) | aucun | leur réseau `*-data` seulement |

- Le réseau `backend` relie la passerelle, Keycloak et les services. Chaque réseau `*-data` est privé à un service
  et à sa base : la règle « une base par service » est imposée par la topologie, pas seulement par discipline.
- `depends_on` avec `condition: service_healthy` : chaque service attend sa base; la passerelle attend les services.
  Un service démarre même si un **autre** service est absent (pas de dépendance de démarrage entre services : la
  relance et le disjoncteur absorbent l'absence).
- Le test « microservice hors passerelle » du projet de démarrage (port non joignable depuis l'hôte) reste vrai
  pour les trois services.
