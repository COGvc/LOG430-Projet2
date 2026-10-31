# Conception — CanDroneX Phase 2

Dossier de travail de la conception. Les diagrammes sont en PlantUML (`docs/diagrams/*.puml`) pour rester
modifiables et discutables en texte. Le rapport (gabarit) reprend ces éléments une fois validés.

| Étape | Fichier | Statut | Sections du gabarit alimentées |
|---|---|---|---|
| 1. UC-06 et modèle d'états | [01-uc06-etats.md](01-uc06-etats.md) | validé (Q1–Q3 acceptées) | §1.2, §6.2 |
| 2. Bounded contexts et context map | [02-contextes.md](02-contextes.md) | validé | §3.3, §4.1, §5.1 |
| 3. Propriété des données et dépendances | [03-donnees.md](03-donnees.md) | validé (orderId lisible, D2 sans jeton) | §4.2, §8.1 |
| 4. Flux, idempotence et défaillances | [04-flux.md](04-flux.md) | validé (appels internes sans jeton, `clientId` explicite) | §6.1, §8.2, §8.3, §8.6 |
| 5. Structure hexagonale et port réseau | [05-hexagonal.md](05-hexagonal.md) | validé | §5.2, §5.3 |
| 6. Passerelle et sécurité | [06-passerelle-securite.md](06-passerelle-securite.md) | proposé | §4.4, §8.5 |
| 7. ADR | — | à faire | §9 |

## Hypothèses de conception retenues (à confirmer)

| # | Hypothèse | Source | Effet |
|---|---|---|---|
| H1 | Une slice est un **abonnement** d'un drone à une tranche préexistante; CanDroneX ne crée ni ne réserve de tranche. | Dossier domaine §1.2, §6.1 | L'entité centrale est le *service instancié*, qui référence un S-NSSAI comme valeur. |
| H2 | Le client B2B est une **identité** (claim `azp` du JWT), pas une entité gérée en Phase 2. | Énoncé, UC-01 hors périmètre | Aucun service ni table « Client »; chaque donnée porte un `clientId`. |
| H3 | Le réseau Docker interne est **de confiance** (consigne du cours) : pas de TLS ni d'authentification entre services. Le jeton du client est validé à la passerelle **et** par chaque service qui le reçoit (énoncé, projet de démarrage). | Cours + énoncé + projet de démarrage | Pendant une requête du client, les appels entre services (D1, D3) transmettent son jeton. En arrière-plan (D2, D4), il n'y a pas de jeton : l'appel passe le `clientId` vers un endpoint `/internal/v1/...`, jamais routé par la passerelle. |
| H4 | L'IMSI est **détenu par un seul service**; il n'est transmis qu'au moment de l'activation, par un endpoint interne, n'est persisté nulle part ailleurs, n'apparaît dans aucune API publique et est masqué dans les logs. | Loi 25, LPRPDE, dossier §3.4 | Résolution tardive `droneId → IMSI` à chaque tentative d'activation. |
| H5 | Les caractéristiques du service sont **figées dans la commande** à la soumission; l'identité réseau est **résolue** à l'activation. | ISED SPB-001-23, dossier §7.1, §9.3 | L'activation applique ce qui a été commandé et ne relit jamais le catalogue. |
| H6 | **Un état par service commandé**; l'état global d'une commande ne masque jamais un échec. | RAC IX / BVLOS, dossier §3.1 | État global dérivé, avec un état `PARTIAL` distinct de `COMPLETED`. |
