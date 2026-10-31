# Étape 1 — UC-06 et modèle d'états

Diagrammes : [uc-phase2.puml](../diagrams/uc-phase2.puml), [etats-service.puml](../diagrams/etats-service.puml),
[etats-commande.puml](../diagrams/etats-commande.puml).

## 1. Vocabulaire minimal

| Terme | Sens retenu |
|---|---|
| **Commande** (*service order*) | Intention contractuelle d'un client pour un drone. Agrégat. Contient un ou plusieurs éléments. |
| **Élément de commande** (*order item*) | Un service demandé (`C2` ou `IMAGERY`) pour un drone, avec ses caractéristiques figées. |
| **Service** (*service instance*) | La réalisation d'un élément de commande : l'abonnement du drone à une tranche, et son état. Une activation concerne exactement un élément de commande. |
| **Acceptation** | Moment où la commande est validée et persistée : horodatage `acceptedAt`. |
| **Activation** | Inscription de la tranche au profil d'abonné du drone, confirmée par le réseau (ici le mock) : horodatage `activatedAt`. |

## 2. UC-06 — Activer et configurer une slice

**Objectif** : abonner le drone à la tranche correspondant au service commandé, et faire refléter à CanDroneX le
résultat réel de cette opération réseau.

**Acteur principal** : CanDroneX (service des slices), déclenché par l'acceptation d'une commande (UC-04).
**Acteur secondaire** : réseau 5G, simulé par `MockOpen5GSAdapter` derrière `NetworkProvisioningPort`.

**Préconditions**
- La commande a été acceptée (UC-04) : le client est authentifié, le drone lui appartient et le service est au catalogue.
- Les caractéristiques du service (sst, sd, dnn, fiveQi, arp, ambrUplink, ambrDownlink) sont figées dans l'élément.

**Déclencheur** : une demande d'activation par élément de commande, transmise par le service des commandes.

### Scénario principal

1. Le service des commandes transmet une **demande d'activation** pour chaque élément : identifiant de l'élément,
   commande, client, drone, type de service, caractéristiques figées, `acceptedAt`.
2. Le service des slices enregistre le service à l'état **PENDING** et accuse réception. La demande est
   idempotente : la clé est l'identifiant de l'élément de commande.
3. Le traitement d'activation prend le service en charge : état **ACTIVATING**.
4. CanDroneX **résout l'identité réseau** du drone (`droneId → IMSI`) auprès du service qui la détient.
5. CanDroneX **construit la configuration** à partir des caractéristiques figées : S-NSSAI (sst + sd), DNN,
   5QI, ARP et AMBR montant et descendant.
6. CanDroneX invoque `NetworkProvisioningPort` pour **abonner le drone à la tranche**. L'écriture est
   conditionnelle sur la clé logique (IMSI, S-NSSAI, DNN) : une tranche déjà présente n'est pas ajoutée de nouveau.
7. Le réseau confirme : le service passe à **ACTIVE**, et `activatedAt` est enregistré.
8. Chaque étape (réception, appel au port, résultat) est journalisée avec l'identifiant de commande.
9. Le client consulte sa commande ou l'inventaire du drone et voit l'état de chaque service, indépendamment.

**Postcondition** : chaque service commandé est ACTIVE ou FAILED, et son état correspond au résultat réel de
l'opération réseau. Le délai d'activation est `activatedAt − acceptedAt`.

### Scénarios alternatifs

| ID | Condition | Traitement | Résultat |
|---|---|---|---|
| **A1** Appel non authentifié | Le client appelle (commande, consultation) sans jeton, avec un jeton expiré ou invalide. | Refus par la passerelle (auth/validator), et par le service s'il était atteint directement. Aucune donnée modifiée, aucune activation déclenchée. | 401, format d'erreur uniforme. |
| **A2** Ressource d'un autre client | Le client consulte la commande ou le service d'un autre client. | Même réponse qu'une ressource inexistante. | 404, sans divulgation. |
| **A3** Échec constaté | À l'étape 6, le réseau refuse explicitement (abonné inconnu, tranche inconnue…). Le mock le simule par le taux d'échec configurable. | Cause consignée (`NETWORK_REJECTED`). L'autre service de la commande n'est pas affecté. | Service **FAILED**; commande PARTIAL ou FAILED. |
| **A4** Issue indéterminée | À l'étape 6, le délai imparti est dépassé ou la réponse est ambiguë. | Cause `NETWORK_INDETERMINATE`, qui signale qu'une vérification est nécessaire avant toute reprise. | Service **FAILED**. Reprise = UC-10, hors périmètre. |
| **A5** Identité non résolue | À l'étape 4, le drone est introuvable ou le service qui détient l'IMSI est indisponible. | Aucun appel réseau. Cause `IDENTITY_UNRESOLVED`. | Service **FAILED**. |
| **A6** Demande répétée | La même demande d'activation (même élément de commande) arrive de nouveau. | Le service existant est retourné tel quel; aucun nouveau traitement, aucun appel réseau. | Pas de doublon. |
| **A7** Interruption | Le service des slices redémarre alors que des services sont PENDING ou ACTIVATING. | **À décider (Q3)**. | — |

## 3. Modèle d'états

### 3.1 État d'un service (un par élément de commande)

```
PENDING ──prise en charge──► ACTIVATING ──confirmation réseau──► ACTIVE
                                 │
                                 └──refus / délai / identité non résolue──► FAILED
```

| État | Sens | Horodatage |
|---|---|---|
| PENDING | Demande d'activation enregistrée, pas encore traitée. C'est la « file d'activation » à surveiller pendant le test de charge. | `acceptedAt` (reçu de la commande) |
| ACTIVATING | Traitement en cours : résolution d'identité et appel au port réseau. | `startedAt` |
| ACTIVE | Tranche inscrite au profil d'abonné, confirmée par le réseau. Terminal en Phase 2. | `activatedAt` |
| FAILED | Échec explicite, avec une cause (`NETWORK_REJECTED`, `NETWORK_INDETERMINATE`, `IDENTITY_UNRESOLVED`). Terminal en Phase 2. | `failedAt` |

Évolutions prévues, hors Phase 2 : `DEACTIVATING` et `TERMINATED` (UC-09), `CANCELLED` (UC-15), et le retour
de FAILED vers PENDING lors d'une reprise (UC-10).

### 3.2 État d'un élément de commande

L'élément de commande **reflète** l'état du service qui le réalise. On ne maintient pas deux machines à états
indépendantes qui pourraient diverger (où vit cette information : étape 2).

### 3.3 État global d'une commande — **dérivé, jamais saisi**

| État global | Règle (sur l'ensemble des éléments) |
|---|---|
| ACKNOWLEDGED | Tous les éléments sont PENDING. |
| IN_PROGRESS | Au moins un élément n'est pas terminal, et au moins un a commencé. |
| COMPLETED | Tous les éléments sont ACTIVE. |
| PARTIAL | Tous sont terminaux, au moins un ACTIVE et au moins un FAILED. |
| FAILED | Tous les éléments sont FAILED. |

Règle BVLOS : `COMPLETED` exige que **tous** les éléments soient actifs. Un C2 en échec avec une imagerie active
donne `PARTIAL`, jamais `COMPLETED`, et la réponse expose toujours l'état de chaque élément.

Note : le cahier des charges propose `RECEIVED → IN_PROGRESS → COMPLETED / FAILED`. On retient le vocabulaire
TMF641 (`ACKNOWLEDGED`, `PARTIAL`) parce que le dossier domaine l'utilise et que `PARTIAL` est indispensable.

## 4. Questions ouvertes

- **Q1** — Une commande peut-elle demander un service déjà ACTIVE pour ce drone (ex. un second C2) ?
  Proposition : la Phase 2 ne l'interdit pas; l'écriture conditionnelle garantit qu'il n'y aura pas de configuration
  réseau en double. Interdire exigerait que le service des commandes interroge l'inventaire, et compliquerait
  le test de charge (180 commandes/s pour seulement 60 nouveaux drones/s).
- **Q2** — Le refus d'une **commande** (drone inconnu, d'un autre client, service hors catalogue) relève d'UC-04,
  pas d'UC-06 : aucune activation n'est créée. D'accord ?
- **Q3** — Interruption (A7). Deux options :
  - (a) au redémarrage, PENDING et ACTIVATING sont **repris** automatiquement. C'est sûr parce que l'écriture réseau
    est conditionnelle : rejouer ne crée pas de doublon, et la relecture tient lieu de vérification.
  - (b) ACTIVATING passe à FAILED (`NETWORK_INDETERMINATE`), et seuls les PENDING sont repris.
  Proposition : (a). Elle sert la capacité de récupération (« une commande ne doit pas être perdue ») sans
  empiéter sur UC-10, qui concerne la reprise d'un échec et non la reprise d'un traitement interrompu.
