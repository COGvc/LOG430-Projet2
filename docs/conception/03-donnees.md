# Étape 3 — Propriété des données et dépendances

Diagramme : [modele-donnees.puml](../diagrams/modele-donnees.puml).

Principes : chaque service possède sa base; aucune clé étrangère entre bases; on ne se réfère à une donnée d'un
autre service que par son identifiant; on échange des DTO conçus à partir du domaine, jamais des lignes de table.

## 1. Identifiants qui traversent les frontières

| Identifiant | Généré par | Portée / unicité | Rôle |
|---|---|---|---|
| `clientId` | Keycloak (claim `azp`) | Global | Propriétaire de toute donnée. Identifiant **opaque** : aucun service ne l'interprète. |
| `droneId` | **Le client** (ex. `DRN-0231`) | Unique **chez un client** : la clé réelle est `(clientId, droneId)` | Référence métier du drone, la seule qui circule entre services. |
| `orderId` | service-ordering-service, lisible (`SO-1001`, par séquence) | Global | Identifiant de commande; présent dans chaque log (corrélation). |
| `orderItemId` | service-ordering-service, dérivé de la commande (`SOI-1001-1`) | Global | **Clé d'idempotence de la demande d'activation.** |
| `serviceId` | slice-management-service (UUID) | Global | Identifiant du service instancié dans l'inventaire. |
| `Idempotency-Key` | Le client (en-tête HTTP) | Unique **chez un client** | Clé d'idempotence de la prise de commande. |

`droneId` est le **seul** identifiant choisi par le client, comme le prévoient le cahier des charges (« identifiant
métier du drone ») et le dossier domaine (« choisi par l'exploitant ») : le drone existe et porte un nom avant
d'arriver chez CanDroneX. Deux clients peuvent donc avoir chacun un `DRN-0231`.

`orderId` est lisible (`SO-1001`, comme la commande de référence du dossier domaine) parce qu'il apparaît dans
les logs, les démos et le rapport. Une séquence suffit : service-ordering-service est le seul à en créer.

L'`Idempotency-Key` n'est **pas** un identifiant de commande : c'est un jeton de requête, généré par le client avant
l'envoi et renvoyé tel quel en cas de nouvel essai. Lui seul sait si deux requêtes sont la même demande logique.

## 2. Modèle logique par service

### drone-management-service — base `drone-db`

| Entité | Attributs | Contraintes |
|---|---|---|
| **Drone** | `clientId`, `droneId`, `simType` (`SIM` \| `ESIM`), `status` (`ACTIVE`), `registeredAt` | Clé `(clientId, droneId)` |
| **Identité réseau** | `clientId`, `droneId`, `imsi`, `iccid` | 1–1 avec Drone; `imsi` **unique** (15 chiffres); `iccid` **unique** (19–20 chiffres) |

L'identité réseau est une **table séparée** : le périmètre des données sensibles se vérifie d'une requête sur le
catalogue système (aucune autre colonne `imsi`/`iccid` dans le système). L'unicité de l'IMSI garantit le
mapping non ambigu `droneId ↔ IMSI` exigé par UC-02.

### service-ordering-service — base `ordering-db`

Deux schémas, un par module (catalogue, commande), pour garder la frontière de contexte visible.

| Entité | Attributs | Contraintes |
|---|---|---|
| **Spécification de service** *(catalogue)* | `serviceType` (`C2` \| `IMAGERY`), `name`, `description` | Clé `serviceType` |
| **Caractéristique par défaut** *(catalogue)* | `serviceType`, `name`, `value` | Clé `(serviceType, name)`. Les 7 caractéristiques sont des **données**, pas des colonnes : ajouter un service ou un paramètre ne change pas le schéma. |
| **Commande** | `orderId`, `clientId`, `acceptedAt`, `idempotencyKey`, `requestFingerprint` | Unique `(clientId, idempotencyKey)` |
| **Élément de commande** | `orderItemId`, `orderId`, `droneId`, `serviceType`, `action` (`add`), `characteristics` (copie figée), `dispatchStatus` (`PENDING` \| `DISPATCHED`), `dispatchAttempts`, `nextDispatchAt` | Unique `(orderId, serviceType)` : un même service n'apparaît qu'une fois par commande; index sur `dispatchStatus` (relance) |

- L'état global de la commande et l'état des éléments ne sont **pas stockés** ici (étape 2).
- `characteristics` est une **copie** des valeurs du catalogue au moment de la soumission (ISED, H5) : une
  évolution du catalogue ne réécrit jamais une commande passée.
- `requestFingerprint` (empreinte du corps de la requête) distingue un rejeu légitime d'une réutilisation
  abusive de la même clé avec un contenu différent.
- `dispatchStatus`, `dispatchAttempts` et `nextDispatchAt` portent la **relance en arrière-plan** de la
  transmission à slice-management-service : l'élément est enregistré `PENDING` dans la même transaction que la commande,
  puis transmis après la réponse au client (étape 4).

### slice-management-service — base `slice-db`

| Entité | Attributs | Contraintes |
|---|---|---|
| **Service** *(inventaire)* | `serviceId`, `orderItemId`, `orderId`, `clientId`, `droneId`, `serviceType`, `characteristics` (copie figée reçue), `state`, `failureCause`, `attempts`, `nextAttemptAt`, `acceptedAt`, `startedAt`, `activatedAt`, `failedAt` | Unique `orderItemId` (**idempotence de l'activation**); index sur `(state, nextAttemptAt)` (file d'activation), sur `(clientId, droneId)` (inventaire par drone) et sur `orderId` (lecture par commande) |

- **Aucune colonne IMSI** : l'identité réseau est résolue à chaque tentative et n'est conservée qu'en mémoire,
  le temps de l'appel au port (H4).
- `characteristics` est reçue de la commande; slice-management-service ne lit jamais le catalogue.
- `acceptedAt` est **reçu** de la commande : le délai d'activation `activatedAt − acceptedAt` se calcule
  entièrement dans `slice-db`, par une seule requête SQL (P95).
- Le profil d'abonné tenu par `MockOpen5GSAdapter` simule l'état du **réseau** : il n'appartient pas à CanDroneX
  et n'est pas dans `slice-db` (il vit dans l'adaptateur, en mémoire).

## 3. Contrats entre services (DTO)

| # | Appelant → appelé | Besoin | Requête | Réponse | Interdit dans le DTO |
|---|---|---|---|---|---|
| D1 | ordering → drone | Le drone existe-t-il et appartient-il au client ? Est-il actif ? | `droneId` + jeton du client transmis (`GET /api/drone-management/v1/drone/{droneId}`) | `DroneDTO` : `droneId`, `simType`, `status`, `registeredAt` | IMSI, ICCID |
| D2 | ordering → slice | Demander l'activation d'un élément | `ActivationRequestDTO` : `orderItemId`, `orderId`, `clientId`, `droneId`, `serviceType`, `characteristics[]`, `acceptedAt` (appel du système, sans jeton : H3) | `ServiceDTO` | IMSI |
| D3 | ordering → slice | État des services d'une commande | `orderId` + jeton du client transmis (`GET /api/slice-management/v1/service?orderId=`) | `ServiceDTO[]` : `serviceId`, `orderItemId`, `droneId`, `serviceType`, `state`, `failureCause`, `acceptedAt`, `activatedAt` | IMSI, détails réseau internes |
| D4 | slice → drone | Résoudre l'identité réseau au moment de l'activation | `clientId`, `droneId` (appel du système, sans jeton : H3) | `NetworkIdentityDTO` : `imsi` | ICCID (inutile au provisionnement) |

- **D1 et D3 sont faits pendant une requête du client** : ils transmettent son jeton inchangé, comme le prévoit
  la convention du projet de démarrage (« les appels entre services […] transmettent `Authorization` et
  `X-Correlation-Id` »). Ils appellent directement l'API versionnée de l'autre service par le réseau Docker, sans
  repasser par la passerelle; l'appelé revalide le jeton et applique la propriété comme pour un appel public.
- **D2 et D4 sont faits en arrière-plan**, sans client derrière : il n'y a **aucun jeton** à transmettre. Ils
  passent le `clientId` explicitement et l'appelé filtre quand même par `clientId` (H3).
- Règle de nommage : **avec jeton → `/api/<contexte>/v1/...`; sans jeton → `/internal/v1/...`**. Les endpoints
  `/internal/v1/...` ne sont **jamais routés** par la passerelle : seuls les services du réseau Docker les atteignent.
- D4 est le **seul** endroit où l'IMSI quitte drone-management-service.

## 4. Graphe de dépendances

```
service-ordering-service ──D1──► drone-management-service
service-ordering-service ──D2, D3──► slice-management-service ──D4──► drone-management-service
```

Aucun cycle. drone-management-service n'appelle personne : c'est le service le plus stable, et celui dont tous dépendent.

| Dépendance | Si l'appelé ne répond pas (principe) |
|---|---|
| D1 (validation du drone) | La commande est **refusée** sans rien persister : on ne peut pas accepter une commande pour un drone non vérifié. |
| D2 (demande d'activation) | Hors du chemin de la requête : la commande est **déjà acceptée et persistée**. L'élément reste « non transmis » et la relance réessaie plus tard. |
| D3 (lecture d'état) | Réponse **dégradée** : la commande est retournée, l'état des éléments est inconnu. |
| D4 (résolution d'identité) | Pas d'appel réseau sans identité : le service retourne en file avec un délai croissant, puis passe en échec (`IDENTITY_UNRESOLVED`, A5) à l'échéance. |

Les délais d'attente, le nombre de tentatives et la façon de transmettre plus tard un élément non transmis
relèvent de l'étape 4.

## 5. Cohérence entre services

- Aucune transaction distribuée. Chaque service est cohérent dans sa base; entre services, la cohérence est
  **éventuelle** et portée par l'idempotence (clé `orderItemId`) : toute transmission peut être répétée sans
  effet de bord.
- Une référence vers une donnée d'un autre service (`droneId` dans une commande) n'est pas garantie par une
  clé étrangère : elle est **vérifiée au moment de l'usage** (D1 à la commande, D4 à l'activation).
- Le retrait d'un drone (UC-14) est hors Phase 2 : il exigerait de vérifier d'abord qu'il ne porte plus de
  service actif (appel à slice-management-service).
