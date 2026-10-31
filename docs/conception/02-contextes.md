# Étape 2 — Bounded contexts et découpage en services

Diagrammes : [context-map.puml](../diagrams/context-map.puml),
[decoupage-services.puml](../diagrams/decoupage-services.puml).

## 1. Bounded contexts

| Contexte | Responsabilité | Langage propre | UC | Type |
|---|---|---|---|---|
| **Gestion des drones** | Enregistrer les drones d'un client et tenir la correspondance non ambiguë `droneId ↔ IMSI`. | drone, `droneId`, IMSI, ICCID, SIM/eSIM, statut, propriétaire | UC-02 | Cœur |
| **Catalogue de services** | Décrire l'offre : ce qui peut être commandé et ses caractéristiques par défaut. | spécification de service, type de service (`C2`, `IMAGERY`), caractéristique | lu par UC-04 | Support |
| **Commande de services** | Recevoir et conserver l'intention contractuelle du client, avec ses caractéristiques figées. | commande, élément de commande, clé d'idempotence, `acceptedAt` | UC-04 | Cœur |
| **Activation** | Abonner le drone à la tranche commandée et traiter le résultat réseau. | demande d'activation, résolution d'identité, configuration réseau, issue (succès, refus, indéterminée) | UC-06 | Cœur |
| **Inventaire de services** | Tenir l'état courant de chaque service instancié, par drone, sans interroger le réseau. | service, état, cause d'échec, `activatedAt` | consultation | Cœur |
| **Accès B2B** *(externe)* | Authentifier l'application cliente et fournir son identité. | client, jeton, `azp`, audience | — | Générique, réalisé par Keycloak |
| **Réseau 5G** *(externe)* | Profil d'abonné et tranches préexistantes. | S-NSSAI, DNN, 5QI, ARP, AMBR, profil d'abonné | — | Système tiers, simulé en Phase 2 |

## 2. Relations entre contextes (context map)

| Amont (fournisseur) | Aval (client) | Relation | Ce qui transite |
|---|---|---|---|
| Gestion des drones | Commande de services | Client / fournisseur; API publiée (*open host service*) | Existence, statut et propriétaire d'un drone. **Jamais l'IMSI.** |
| Gestion des drones | Activation | Client / fournisseur; endpoint interne | Identité réseau (`IMSI`) au moment de l'activation, uniquement. |
| Catalogue de services | Commande de services | Client / fournisseur, dans le même service | Caractéristiques par défaut, **copiées** dans l'élément de commande à la soumission. |
| Activation / Inventaire | Commande de services | Client / fournisseur; API publiée | Demande d'activation (aller); état de chaque service (lecture). |
| Réseau 5G | Activation | **Couche anticorruption** (`NetworkProvisioningPort` + adaptateur) | Abonnement du drone à une tranche. Le modèle Open5GS ne pénètre jamais le domaine. |
| Accès B2B (Keycloak) | Tous | **Conformiste** | Jeton JWT; on adopte son modèle (claim `azp`, audience `candronex-api`) sans le négocier. |

Le sens des relations ne contient pas de cycle : Commande → Drones, Commande → Activation/Inventaire,
Activation → Drones.

## 3. Regroupement en microservices

| Microservice | Contextes | Données possédées (base propre) | Justification |
|---|---|---|---|
| **drone-management-service** | Gestion des drones | drones, identité réseau (IMSI, ICCID) | Seul détenteur de l'IMSI : le périmètre des données sensibles est un seul service et un seul schéma (Loi 25, LPRPDE). |
| **service-ordering-service** | Catalogue + Commande | spécifications de service, commandes, éléments (caractéristiques figées), clés d'idempotence | La commande ne lit le catalogue qu'à la soumission et en copie les valeurs. Deux modules distincts dans le même service : la frontière de contexte est conservée dans le code, sans payer un appel réseau. |
| **slice-management-service** | Activation + Inventaire | services instanciés, états, causes, horodatages | L'inventaire est exactement l'état produit par l'activation. Les séparer exigerait de synchroniser deux services sans courtier de messages. Porte UC-06, `NetworkProvisioningPort` et `MockOpen5GSAdapter`. |

**Trois services** : le plus petit nombre qui garde séparés les trois rythmes de changement du domaine
(le parc de drones, l'intention contractuelle, la réalisation réseau) et confine l'IMSI.

### Où vit l'état d'un élément de commande

**Décision** : l'état d'un élément de commande **n'est stocké qu'une fois**, dans slice-management-service (inventaire).
service-ordering-service le lit au moment de la consultation et calcule l'état global de la commande à ce moment-là.

| Option | Avantage | Inconvénient | Retenue |
|---|---|---|---|
| État stocké dans slice-management-service, lu par service-ordering-service | Une seule source de vérité; aucun cycle; aucune divergence possible. | Consulter une commande coûte un appel à slice-management-service; si slice-management-service est indisponible, l'état des éléments est inconnu (réponse dégradée à définir à l'étape 4). | ✅ |
| slice-management-service rappelle service-ordering-service à chaque changement d'état | La commande se lit seule. | **Cycle** de dépendances (interdit par l'énoncé); deux copies de l'état qui peuvent diverger. | ❌ |
| Commande et activation dans le même service | Aucun appel entre les deux. | Contredit le dossier domaine (§13.1) : l'agrégat le plus lourd du système, inextractible; le service des slices exigé par l'énoncé disparaît. | ❌ |

En Phase 3, un courtier d'événements permettra à service-ordering-service de tenir une projection de l'état, alimentée
par des événements `ServiceActivated` / `ServiceActivationFailed`, sans créer de dépendance d'appel inverse.

## 4. Options de découpage écartées

| Option | Raison du rejet |
|---|---|
| **Service client dès la Phase 2** | Aucune donnée ni règle propre : l'identité vient du jeton (`azp`), UC-01 est hors périmètre. Un service vide contredit « le plus petit nombre de services ». La porte reste ouverte : `clientId` est un identifiant opaque porté par chaque donnée, la lecture de l'identité est isolée dans un adaptateur d'entrée, et le `client-management-service` prévu en Phase 3 pourra être appelé par service-ordering-service sans créer de cycle. |
| **Service catalogue séparé** | Rythme de changement distinct (licences de spectre), mais la commande n'en a besoin qu'à la soumission et en copie les valeurs. Le séparer ajouterait un appel synchrone sur le chemin de chaque commande sans bénéfice en Phase 2. À reconsidérer si le catalogue gagne une couche produit ou un cycle de publication propre. |
| **Inventaire séparé de l'activation** | Exigerait une synchronisation entre deux services sans courtier, avec un risque d'écart permanent. |
| **Un service par contexte (5 services)** | Respecte la lettre du DDD mais multiplie les appels synchrones et les points de panne pour aucun gain mesurable en Phase 2. |

## 5. Ce que la Phase 2 prépare

| Évolution | Point d'ancrage dans le découpage actuel |
|---|---|
| Onboarding B2B (UC-01) | Contexte Accès B2B déjà nommé; `clientId` opaque partout. |
| Open5GS réel | Remplacer `MockOpen5GSAdapter` par un adaptateur réel derrière le même port. |
| Architecture événementielle | Les transitions d'état de slice-management-service deviennent des événements; service-ordering-service passe de la lecture synchrone à une projection. |
| Séparer le catalogue | Déjà un module distinct dans service-ordering-service, avec son propre schéma. |
