# Étape 5 — Structure hexagonale et port réseau

Diagrammes : [hexa-slice-service.puml](../diagrams/hexa-slice-service.puml) (détaillé),
[hexa-services.puml](../diagrams/hexa-services.puml) (drone-management-service et service-ordering-service).

Alimente le gabarit : §5.2 (vue Logique, découpage interne), §5.3 (vue Développement).

## 1. Principe

Chaque service a la même structure en quatre parties. Les dépendances vont **toujours vers le centre** :

| Partie | Contenu | Peut importer |
|---|---|---|
| **Domaine** | Entités, objets valeur, règles et transitions d'état, erreurs métier. Python pur. | Bibliothèque standard seulement |
| **Application** | Cas d'utilisation (ports d'entrée), définition des ports de sortie, politiques (délais, échéances, reculs). | Domaine |
| **Adaptateurs** | Entrée : ce qui déclenche un cas d'utilisation (REST, relance, traitement d'activation). Sortie : ce qui réalise un port (PostgreSQL, clients HTTP, mock réseau). | Application, domaine, frameworks |
| **Point d'assemblage** | Construit les adaptateurs, les injecte dans les cas d'utilisation, démarre l'application. Seul endroit qui lit la configuration. | Tout |

Conséquences :
- Le domaine et l'application ne connaissent ni FastAPI, ni asyncpg, ni httpx, ni JWT.
- Un cas d'utilisation se teste avec des faux en mémoire pour ses ports, sans base ni réseau.
- Changer de technologie (Open5GS réel, Kafka) remplace un adaptateur et une ligne d'assemblage, pas le centre.

Les ports sont des `typing.Protocol` : l'adaptateur n'hérite de rien, il respecte la signature.

## 2. Nommer les ports selon le besoin

Un port décrit **ce dont le cas d'utilisation a besoin**, pas la technologie qui le fournit :
`DroneNetworkIdentityPort.resolve(...)` et non `DroneServiceHttpClient.get(...)`. Si D4 passe un jour par un
message ou un cache, le nom reste juste.

## 3. slice-management-service (détaillé)

### 3.1 Domaine

| Élément | Nature | Rôle |
|---|---|---|
| `ServiceInstance` | Entité | Un service commandé : `serviceId`, `orderItemId`, `clientId`, `droneId`, `serviceType`, `characteristics`, `state`, `failureCause`, `attempts`, `nextAttemptAt`, horodatages. Ses méthodes sont les **seules** transitions permises : `start_activation()`, `activate(at)`, `fail(cause)`, `postpone(until)`. Une transition illégale lève `IllegalTransition`. |
| `ServiceState`, `FailureCause` | Énumérations | `PENDING`, `ACTIVATING`, `ACTIVE`, `FAILED`; `NETWORK_REJECTED`, `NETWORK_INDETERMINATE`, `IDENTITY_UNRESOLVED`. |
| `ServiceCharacteristics` | Objet valeur, immuable | Ce qui a été commandé, figé (H5) : S-NSSAI, DNN, 5QI, ARP, AMBR. |
| `Snssai`, `Dnn`, `QosProfile`, `Ambr` | Objets valeur | Valident leur forme à la construction (sst 0–255, sd 6 hexadécimaux, 5QI connu…). |
| `Imsi` | Objet valeur | 15 chiffres. Sa représentation textuelle est **masquée** (`999700000***231`) : un `log(imsi)` accidentel ne divulgue rien (H4). La valeur complète ne sort que par une méthode explicite, appelée par l'adaptateur réseau. |
| `SliceConfiguration` | Objet valeur | Ce qu'on écrit dans le réseau : `Imsi` + caractéristiques. Construit en mémoire à chaque tentative, jamais persisté. |
| `ProvisioningOutcome` | Objet valeur | `SUCCEEDED`, `REJECTED(reason)` ou `INDETERMINATE(reason)`. |

### 3.2 Application — ports d'entrée (cas d'utilisation)

| Cas d'utilisation | Déclenché par | Effet |
|---|---|---|
| `RegisterActivationRequest` | D2 (API interne) | Crée le `ServiceInstance` en `PENDING`, ou retourne l'existant si `orderItemId` est déjà connu (idempotence). |
| `ActivateNextDueService` | Traitement d'activation (boucle) | Prend un service échu, applique UC-06 (§3.4). |
| `GetServicesOfOrder` | D3 (API versionnée, avec jeton) | Services d'une commande, filtrés par `clientId`. |
| `GetDroneInventory` | API versionnée (client, par la passerelle) | Services d'un drone du client (inventaire). |
| `ResumeInterruptedActivations` | Démarrage | Remet en `PENDING` les `ACTIVATING` orphelins (Q3, étape 1). |

### 3.3 Application — ports de sortie

| Port | Méthodes | Réalisé par |
|---|---|---|
| `ServiceInventoryRepository` | `add_if_absent(service)`, `claim_next_due(now)`, `save(service)`, `find_by_order(clientId, orderId)`, `find_by_drone(clientId, droneId)`, `release_interrupted()` | `PostgresServiceInventory` (asyncpg). `claim_next_due` utilise `SELECT … FOR UPDATE SKIP LOCKED` : plusieurs traitements en parallèle ne prennent jamais le même service. |
| `DroneNetworkIdentityPort` | `resolve(clientId, droneId) -> Imsi` | `HttpDroneNetworkIdentity` (D4). Lève `IdentityNotFound` (définitif) ou `DependencyUnavailable` (technique). |
| `NetworkProvisioningPort` | `subscribe(config: SliceConfiguration) -> ProvisioningOutcome` | `MockOpen5GSAdapter` en Phase 2, `Open5GSAdapter` en Phase 3. |
| `Clock` | `now()` | Horloge système; horloge fixe dans les tests (échéance de 30 s, reculs). |

### 3.4 Le cas d'utilisation central : `ActivateNextDueService`

```
service = inventory.claim_next_due(now)            # → ACTIVATING
si now > service.acceptedAt + 30 s : fail(IDENTITY_UNRESOLVED); fin   # seul D4 remet en file
imsi = identity.resolve(clientId, droneId)
    IdentityNotFound        → fail(IDENTITY_UNRESOLVED); fin
    DependencyUnavailable   → postpone(now + recul(attempts)); fin      # retour en file
config = SliceConfiguration(imsi, service.characteristics)
outcome = attendre au plus 3 s : network.subscribe(config)
    délai dépassé ou erreur technique → INDETERMINATE
SUCCEEDED     → activate(now)
REJECTED      → fail(NETWORK_REJECTED)
INDETERMINATE → fail(NETWORK_INDETERMINATE)          # pas de reprise aveugle (étape 4)
inventory.save(service)
```

### 3.5 Décision : qui applique le délai de l'appel réseau ?

**La couche application**, avec `asyncio.wait_for`, et non l'adaptateur.

- Le délai de 3 s n'est pas un détail technique : il **décide d'une issue métier** (`INDETERMINATE`) et de l'état
  final du service. C'est une politique, au même titre que l'échéance de 30 s.
- L'adaptateur réel de la Phase 3 n'aura ni délai ni traduction d'erreurs à réimplémenter : il retourne
  `SUCCEEDED` ou `REJECTED`, ou lève une exception technique. Le comportement en cas de lenteur est garanti
  identique quel que soit l'adaptateur.

Les délais des appels **HTTP** entre services (D1 à D4), eux, restent dans les adaptateurs clients : ils ne
produisent pas d'issue métier, seulement `DependencyUnavailable`, et dépendent du transport. Leurs valeurs viennent
de la configuration, lue au point d'assemblage.

### 3.6 Le disjoncteur : un adaptateur qui en enveloppe un autre

Le disjoncteur (étape 4) est un **décorateur** qui réalise le même port que l'adaptateur qu'il enveloppe :

```
DroneNetworkIdentityPort  ←  CircuitBreaker( HttpDroneNetworkIdentity )
```

Ouvert, il lève `DependencyUnavailable` sans appeler. Le cas d'utilisation n'en sait rien; il traite cette erreur
comme les autres (retour en file). Même montage pour `ActivationRequestPort` (D2) dans service-ordering-service.

### 3.7 `MockOpen5GSAdapter`

| Aspect | Comportement |
|---|---|
| Latence | Aléatoire, 300 à 1500 ms (configurable). |
| Taux d'échec | `MOCK_FAILURE_RATE` (0 à 1). À 1, tout est `REJECTED` : sert au test « activation en échec ». |
| Écriture conditionnelle | Abonnements en mémoire, clés `(IMSI, S-NSSAI, DNN)`. Même clé et même configuration → `SUCCEEDED` sans rien réécrire (troisième niveau d'idempotence). |
| Confidentialité | Ne journalise que l'IMSI masqué. |
| Frontière | Aucune connexion réseau, aucun cœur 5G : c'est un adaptateur interne au slice-management-service (énoncé). |

Le choix de l'adaptateur se fait au point d'assemblage par une variable `NETWORK_ADAPTER=mock`. En Phase 3,
`Open5GSAdapter` réalisera le même port; **rien d'autre ne change**.

### 3.8 Adaptateurs d'entrée

| Adaptateur | Déclenche | Détail |
|---|---|---|
| API versionnée `/api/slice-management/v1` (FastAPI) | `GetDroneInventory`, `GetServicesOfOrder` | Valide le jeton, extrait `clientId` (§6). Appelée par la passerelle (inventaire) et par service-ordering-service (D3). |
| API interne `/internal/v1` (FastAPI) | `RegisterActivationRequest` | Sans jeton (H3); `clientId` dans la requête. |
| Traitement d'activation | `ActivateNextDueService` | N tâches asyncio en boucle; attend un court instant quand rien n'est échu. N configurable (concurrence de l'activation). |
| Démarrage | `ResumeInterruptedActivations` | Appelé une fois au lancement, avant le traitement d'activation. |

## 4. drone-management-service

| Partie | Contenu |
|---|---|
| Domaine | `Drone` (`clientId`, `droneId`, `simType`, `status`, `registeredAt`), `NetworkIdentity` (`Imsi`, `Iccid`), `SimType`, objets valeur validés (IMSI 15 chiffres, ICCID 19–20 chiffres), `DroneAlreadyRegistered`. |
| Ports d'entrée | `RegisterDrone`, `GetDrone`, `ListDrones` (API versionnée : client par la passerelle, et D1); `ResolveNetworkIdentity` (D4, interne). |
| Ports de sortie | `DroneRepository` : `add(drone, identity)` (une transaction, unicité vérifiée par la base), `find(clientId, droneId)`, `list(clientId)`, `find_identity(clientId, droneId)`. |
| Adaptateurs | API publique (avec jeton), API interne (sans jeton), `PostgresDroneRepository`. |

Les réponses publiques et D1 sont construites à partir de `Drone` seulement : `NetworkIdentity` n'y figure pas, il
est donc **impossible** de divulguer l'IMSI par erreur de sérialisation. Seul `ResolveNetworkIdentity` le lit.

## 5. service-ordering-service

Deux **modules** dans un même service (étape 2) : `catalog` et `ordering`. `ordering` ne lit le catalogue que par
l'interface publique de `catalog` (un port d'entrée `GetServiceSpecification`), jamais par ses tables.

| Partie | Contenu |
|---|---|
| Domaine — catalog | `ServiceSpecification` (`serviceType`, caractéristiques en données). |
| Domaine — ordering | `Order` (`orderId`, `clientId`, `idempotencyKey`, `requestFingerprint`, `acceptedAt`), `OrderItem` (caractéristiques figées, `dispatchStatus`, `dispatchAttempts`, `nextDispatchAt`), `derive_order_state(états des éléments)` : la règle de l'état global (`COMPLETED`, `PARTIAL`, `FAILED`, `IN_PROGRESS`, `ACKNOWLEDGED`, `UNKNOWN`) est une **fonction pure du domaine**. |
| Ports d'entrée | `ListCatalog`, `PlaceOrder`, `GetOrder`, `ListOrders` (publics); `DispatchPendingItems` (relance). |
| Ports de sortie | `OrderRepository` (`add` avec unicité `(clientId, idempotencyKey)`, `find`, `claim_due_items`, `mark_dispatched`, `postpone`), `CatalogRepository`, `DroneEligibilityPort` (D1 : `check(clientId, droneId) -> Eligibility`), `ActivationRequestPort` (D2 : `request(ActivationRequest)`), `ServiceStatusPort` (D3 : `states_of(clientId, orderId)`), `OrderIdGenerator` (séquence → `SO-1001`), `Clock`. |
| Adaptateurs | API publique (avec jeton), relance (boucle asyncio), `PostgresOrderRepository`, `PostgresCatalogRepository`, clients HTTP D1/D2/D3, disjoncteur sur D2. |

`PlaceOrder` en bref : clé connue ? (même empreinte → réponse d'origine; autre → `IdempotencyKeyReused`) →
`DroneEligibilityPort.check` → spécifications du catalogue → `Order` + éléments figés → `OrderRepository.add` (une
transaction). Aucun appel à slice-management-service.

## 6. L'identité du client : isolée dans un adaptateur d'entrée

La validation du JWT (signature, `exp`, `iss`, `aud`) et la lecture de `azp` sont faites par une **dépendance
FastAPI** de l'API publique, qui produit un objet valeur `ClientId`. Les cas d'utilisation reçoivent `ClientId` en
paramètre; ils ne voient jamais de jeton.

- Pour D1 et D3, le jeton reçu est placé dans le contexte de la requête (une `contextvar`, comme l'identifiant de
  corrélation); le client HTTP sortant le recopie dans `Authorization`. Le port reste `check(clientId, droneId)` :
  la transmission du jeton est un détail technique des adaptateurs, invisible du cas d'utilisation.
- Les API internes produisent le même `ClientId`, lu dans la requête (H3). Le cas d'utilisation ne sait pas d'où il
  vient : un seul code de règle de propriété.
- Si un `client-management-service` apparaît en Phase 3 (étape 2), ou si la claim change, seul cet adaptateur change.

## 7. Code commun entre services

Une petite bibliothèque **technique** partagée, `candronex_common`, installée dans chaque image :
validation du JWT, format d'erreur JSON uniforme, propagation de `X-Correlation-Id`, journalisation structurée,
disjoncteur.

Elle ne contient **aucun** élément de domaine : chaque service garde ses propres entités et DTO, même quand ils se
ressemblent (un `DroneDTO` côté ordering n'est pas le `Drone` de drone-management-service). Partager du domaine couplerait les
déploiements; partager de la technique évite trois copies divergentes de la validation du jeton. Candidat à un ADR
(étape 7).

## 8. Vue Développement

### 8.1 Arborescence (même forme pour chaque service)

```
services/
  common/                       candronex_common (technique seulement)
  slice-management-service/
    app/
      domain/                   service.py, network.py, errors.py
      application/
        ports.py                ports de sortie (Protocol)
        use_cases/              register_activation.py, activate_service.py, queries.py, resume.py
        policies.py             délais, échéance, recul, paramètres du disjoncteur
      adapters/
        inbound/                http_public.py, http_internal.py, activation_worker.py
        outbound/               postgres_inventory.py, http_drone_identity.py, mock_open5gs.py
      bootstrap.py              point d'assemblage (configuration, injection, démarrage)
    tests/
      unit/                     domaine et cas d'utilisation, faux en mémoire
      integration/              adaptateurs contre PostgreSQL
    Dockerfile
    pyproject.toml              dépendances + contrats d'import
  drone-management-service/  …             même forme
  service-ordering-service/
    app/catalog/  …             domain / application / adapters
    app/ordering/ …             domain / application / adapters
tests/e2e/                      scénarios par la passerelle (jeton, refus, activation, échec, doublon)
load/                           script k6
```

### 8.2 Règle de dépendance vérifiée automatiquement

Avec **import-linter**, déclaré dans `pyproject.toml` et exécuté avec les tests :

| Contrat | Vérifie |
|---|---|
| Couches | `adapters` → `application` → `domain`, jamais l'inverse. |
| Domaine pur | `domain` n'importe ni `fastapi`, ni `asyncpg`, ni `httpx`, ni `jwt`. |
| Modules d'ordering | `ordering` n'importe de `catalog` que `catalog.application`. |

Une violation fait échouer la commande de test : l'architecture ne dépend pas de la discipline seule.

### 8.3 Correspondance avec les tests exigés

| Test exigé par l'énoncé | Niveau | Ce qui est remplacé |
|---|---|---|
| Activation réussie, activation en échec | Unitaire sur `ActivateNextDueService` + bout en bout (`MOCK_FAILURE_RATE=1`) | Unitaire : faux `NetworkProvisioningPort` et `DroneNetworkIdentityPort` |
| Répétition sans doublon | Unitaire sur `PlaceOrder` et `RegisterActivationRequest` + bout en bout | Faux dépôts en mémoire |
| Appel sans jeton, données d'un autre client | Bout en bout par la passerelle | Rien : vrai Keycloak, jetons `inspectra-demo` et `aerotech-demo` |

## 9. Ce que la structure prépare pour la Phase 3

| Changement de Phase 3 | Ce qui change | Ce qui ne change pas |
|---|---|---|
| Open5GS réel | Nouvel `Open5GSAdapter` + `NETWORK_ADAPTER=open5gs` | Domaine, `ActivateNextDueService`, délai de 3 s, états |
| Courtier de messages | `ActivationRequestPort` réalisé par un éditeur; l'API interne D2 remplacée par un consommateur | `PlaceOrder`, `RegisterActivationRequest` |
| Service de gestion des clients | L'adaptateur d'identité (§6) | Les cas d'utilisation, qui reçoivent toujours un `ClientId` |
