# Décisions prises pendant l'implémentation

Choix faits en codant, qui ne figurent pas (ou pas exactement) dans `docs/conception/`. À reprendre dans le rapport
(§8 concepts transversaux, §10 mesures, ou ADR).

| # | Décision | Pourquoi | Où |
|---|---|---|---|
| I1 | **Listes paginées** : `GET drone` et `GET serviceOrder` prennent `?limit=` (100 par défaut, 1000 max), triées du plus récent au plus ancien. Le filtre par `clientId` (azp du jeton validé) s'applique toujours avant. | Le test de charge n'utilise que 2 clients et enregistre ~60 drones/s : sans limite, chaque lecture de liste renverrait ~10 000 éléments. | drone-management-service, service-ordering-service |
| I2 | **PostgreSQL avec `synchronous_commit=off`** et `max_connections=300`. | Débit d'écriture sous 600 ops/s. Risque : perdre les dernières transactions validées si PostgreSQL plante (pas d'incohérence). Acceptable pour l'environnement de mesure; à retirer en production. | docker-compose.yml |
| I3 | **Codes d'erreur en français**, format `{erreur, message, correlationId}`, comme le projet de démarrage. | Cohérence avec le code fourni. | candronex_common/erreurs.py |
| I4 | **`krakend.json` généré** par `krakend/generer.py`. | Huit routes presque identiques : une seule définition de la validation du JWT et des en-têtes. | krakend/ |
| I5 | **Secrets** : `.env` ignoré par Git; `.env.example` versionné avec des valeurs vides (y compris les secrets de démo du projet de démarrage). | « Aucun secret dans le dépôt remis » (énoncé). | racine |
| I6 | **Unicité d'un élément de commande : `(orderId, droneId, serviceType)`** au lieu de `(orderId, serviceType)` (étape 3). | Une commande peut viser plusieurs drones; la même prestation pour deux drones est légitime. | service-ordering-service |
| I7 | **Prise en charge par bail** (relance et activation) : un élément est réservé en repoussant `next*At` de 30 s (`FOR UPDATE SKIP LOCKED`), puis traité hors transaction. | Aucune transaction ouverte pendant un appel HTTP; un processus qui meurt libère l'élément à l'échéance du bail (reprise au redémarrage sans traitement spécial). | relance, activation |
| I8 | **Réveil immédiat** de la relance après chaque commande (événement en mémoire), sondage de la base toutes les 200 ms en secours. | Le P95 inclut l'attente avant transmission : on ne veut pas attendre un intervalle de sondage. | service-ordering-service |
