# Étape 4 — Flux, idempotence et défaillances

Diagrammes : [seq-uc02.puml](../diagrams/seq-uc02.puml), [seq-uc04-uc06.puml](../diagrams/seq-uc04-uc06.puml),
[seq-refus.puml](../diagrams/seq-refus.puml), [seq-defaillances.puml](../diagrams/seq-defaillances.puml).

## 1. Principes de communication

### 1.1 Chemin synchrone le plus court possible

Une requête du client ne traverse qu'**un seul** appel entre services au maximum :

| Requête du client | Appels synchrones entre services | Travail fait après la réponse |
|---|---|---|
| UC-02 enregistrer un drone | aucun | — |
| UC-04 créer une commande | D1 (drone existe et appartient au client) | D2 par la relance; UC-06 par le traitement d'activation |
| Consulter une commande | D3 (état des services) | — |
| Consulter l'inventaire d'un drone | aucun (lu dans slice-management-service) | — |

La création d'une commande **n'attend pas** slice-management-service : la commande est persistée, la réponse part, puis la
transmission (D2) se fait en arrière-plan. Il n'y a donc pas d'empilement de délais sur le chemin du client.

Sur le chemin synchrone, D1 et D3 transmettent le jeton du client; D2 et D4, en arrière-plan, n'en ont pas et
passent le `clientId` (H3, [03-donnees.md §3](03-donnees.md)). La revalidation du jeton par l'appelé ne fait pas
d'appel réseau : la clé publique de Keycloak est en cache dans chaque service.

### 1.2 Délais d'attente : un budget décroissant

Un délai d'attente borne le temps qu'un appelant passe à attendre un service qui ne répond plus; il ne coûte
rien quand l'appelé se porte bien. Les délais **décroissent** de l'extérieur vers l'intérieur, pour qu'un appelant
n'abandonne jamais avant ses propres appels internes.

| Appel | Délai | Justification |
|---|---|---|
| Passerelle → service | 5 s | La plus longue, à l'extérieur. |
| D1, D3 (lecture par clé, pendant une requête) | 1 s | Lecture indexée de quelques ms : 1 s ≈ 50 fois la durée normale. |
| D2 (relance, arrière-plan) | 2 s | Pas de client en attente; écriture idempotente. |
| D4 (identité, arrière-plan) | 1 s | Lecture par clé. |
| Port réseau (`NetworkProvisioningPort`) | 3 s | Au-dessus de la latence maximale du mock (1,5 s), dans le budget P95 de 5 s. Dépassement → issue indéterminée. |

Ces valeurs sont des **paramètres** (variables d'environnement). Elles seront recalibrées sur les latences
mesurées pendant le test de charge.

### 1.3 Reprises : jamais pendant une requête, un seul niveau, avec recul

Des reprises rapides sur un service en difficulté **aggravent** sa surcharge, et des reprises à plusieurs niveaux se
multiplient (3 × 3 = 9 appels). Règles retenues :

1. **Aucune reprise pendant une requête du client.** Si D1 ou D3 échoue, on répond immédiatement (503 ou réponse
   dégradée). Le client décide de réessayer; l'`Idempotency-Key` rend ce rejeu sûr.
2. **Reprises seulement en arrière-plan, à un seul niveau** : la relance (D2) et le traitement d'activation (D4).
   Le client HTTP sous-jacent ne réessaie jamais de lui-même.
3. **Délai croissant avec part aléatoire** entre les essais (*exponential backoff with jitter*) : 200 ms, puis
   ×2 à chaque essai, plafonné à 5 s, avec un tirage aléatoire dans l'intervalle. Les essais ne reviennent pas
   tous en même temps sur le service qui récupère.
4. **On ne boucle pas** : un élément ou un service en échec temporaire retourne dans sa file avec une heure de
   prochaine tentative (`nextDispatchAt`, `nextAttemptAt`). La file joue le rôle de tampon.
5. **Échéance** pour l'activation : si l'identité n'est toujours pas résolue 30 s après `acceptedAt`, le service
   passe à FAILED (`IDENTITY_UNRESOLVED`). Pour la transmission D2, **pas d'échéance** : une commande acceptée ne
   doit pas être perdue; l'élément reste non transmis et visible comme tel tant que slice-management-service est absent.

### 1.4 Disjoncteur

Le traitement d'activation (vers drone-management-service, D4) et la relance (vers slice-management-service, D2) passent chacun par un
disjoncteur :

| État | Comportement |
|---|---|
| Fermé | Les appels passent normalement. |
| Ouvert | Après 5 échecs consécutifs : plus aucun appel pendant 5 s; aucun nouvel élément n'est pris en charge. Le service en difficulté peut récupérer. |
| Semi-ouvert | Après 5 s : un seul appel d'essai. Succès → fermé; échec → ouvert de nouveau. |

Les échecs pris en compte sont les erreurs techniques (connexion refusée, délai dépassé, 5xx), jamais les
réponses métier (404 drone introuvable).

### 1.5 Reprise après redémarrage

Au démarrage de slice-management-service, les services `PENDING` et `ACTIVATING` sont repris (décision Q3, étape 1).
C'est sûr parce que l'écriture réseau est conditionnelle. Au démarrage d'service-ordering-service, la relance reprend
naturellement les éléments `PENDING` : ils sont en base.

## 2. Idempotence : trois niveaux

| Niveau | Clé | Mécanisme | Rejeu |
|---|---|---|---|
| **Prise de commande** (UC-04) | `(clientId, Idempotency-Key)` | Contrainte d'unicité, dans la même transaction que la commande; empreinte du corps (`requestFingerprint`). | Même clé + même corps → **200** avec la commande existante, aucun nouvel élément. Même clé + corps différent → **422** `CLE_IDEMPOTENCE_REUTILISEE`. Clé absente → **400**. Deux requêtes simultanées → l'unicité départage, la seconde relit la première. |
| **Demande d'activation** (D2) | `orderItemId` | Contrainte d'unicité dans `slice-db`; insertion « si absente ». | Le service existant est retourné (**200**), quel que soit son état; aucun nouveau traitement. La relance peut donc renvoyer sans risque. |
| **Configuration réseau** (port) | `(IMSI, S-NSSAI, DNN)` | Écriture conditionnelle dans le profil d'abonné (simulé par le mock). | Une tranche déjà présente n'est pas ajoutée de nouveau; succès retourné. Rend sûre la reprise après redémarrage. |

L'énoncé exige l'absence de doublon de commande, d'association slice ↔ drone et de configuration réseau : chacun
des trois niveaux couvre l'un de ces doublons.

## 3. Défaillances et réponses

| Situation | Réponse au client | Effet sur l'état | Diagramme |
|---|---|---|---|
| Jeton absent, expiré ou invalide | 401, refusé par la passerelle (sans corps : limite de KrakenD CE, étape 6) | Aucun | seq-refus |
| Service appelé par la passerelle arrêté ou injoignable | 500 sans corps (limite de KrakenD CE, étape 6) | Aucun | — |
| Ressource d'un autre client | 404 (comme inexistante) | Aucun | seq-refus |
| Drone inconnu ou d'un autre client, à la commande | 422 `DRONE_INTROUVABLE` | Aucune commande créée | — |
| Service hors catalogue | 422 `TYPE_SERVICE_INCONNU` | Aucune commande créée | — |
| drone-management-service indisponible pendant la commande (D1) | 503 `DEPENDANCE_INDISPONIBLE` | Aucune commande créée; le client peut rejouer avec la même clé | seq-defaillances ① |
| slice-management-service indisponible après la commande (D2) | 201 (déjà répondu) | Élément `PENDING` non transmis; relance avec recul et disjoncteur | seq-defaillances ② |
| slice-management-service indisponible pendant une consultation (D3) | 200, réponse **dégradée** : éléments non transmis = `PENDING` (connu d'ordering); autres = `UNKNOWN`; état global `UNKNOWN` | Aucun | seq-defaillances ③ |
| Réseau refuse (mock, taux d'échec) | — (consultation) | Service FAILED `NETWORK_REJECTED`; l'autre service continue | seq-defaillances ④ |
| Réseau ne répond pas dans les 3 s | — | Service FAILED `NETWORK_INDETERMINATE` | seq-defaillances ④ |
| drone-management-service indisponible pendant l'activation (D4) | — | Service retourne `PENDING` avec recul; disjoncteur; à 30 s → FAILED `IDENTITY_UNRESOLVED` | seq-defaillances ⑤ |
| slice-management-service redémarre en cours d'activation | — | `PENDING` et `ACTIVATING` repris au démarrage | — |

Le format JSON des erreurs et les réponses de la passerelle quand un service est indisponible sont définis à
l'étape 6.

## 4. Journalisation le long d'un flux

Chaque ligne contient l'horodatage, le nom du service, l'identifiant de corrélation et, dès qu'il existe,
l'`orderId`. Les événements journalisés au minimum :

| Service | Événement |
|---|---|
| service-ordering-service | commande reçue; commande acceptée ou refusée (motif); rejeu reconnu; élément transmis; échec de transmission |
| slice-management-service | demande d'activation reçue (ou rejouée); prise en charge; appel au `NetworkProvisioningPort`; activation réussie ou échouée (cause); disjoncteur ouvert ou fermé |
| drone-management-service | drone enregistré; identité réseau résolue (IMSI **masqué**) |

`docker compose logs | grep SO-1001` retrace une commande d'un service à l'autre.
