"""Adaptateurs de sortie HTTP : D1 (drone), D2 (demande d'activation), D3 (état des services)."""
import httpx

from candronex_common.clienthttp import DependanceIndisponible, Disjoncteur, entetes, envoyer

from .domain import Commande, ElementCommande


class EligibiliteDroneHttp:
    """D1 : API versionnée de drone-management-service, avec le jeton du client."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def existe(self, client_id: str, drone_id: str) -> bool:
        r = await envoyer(self.client, "GET", f"/api/drone-management/v1/drone/{drone_id}",
                          headers=entetes(transmettre_jeton=True))
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
        raise DependanceIndisponible(f"D1 : réponse {r.status_code}")


class DemandeActivationHttp:
    """D2 : endpoint interne, sans jeton (ADR-002), derrière un disjoncteur."""

    def __init__(self, client: httpx.AsyncClient, disjoncteur: Disjoncteur):
        self.client, self.disjoncteur = client, disjoncteur

    async def demander(self, commande: Commande, element: ElementCommande) -> bool:
        corps = {
            "orderItemId": element.order_item_id,
            "orderId": commande.order_id,
            "clientId": commande.client_id,
            "droneId": element.drone_id,
            "serviceType": element.service_type,
            "characteristics": element.characteristics,
            "acceptedAt": commande.accepted_at.isoformat(),
        }
        r = await self.disjoncteur.appeler(
            envoyer, self.client, "POST", "/internal/v1/activation", json=corps,
            headers=entetes(transmettre_jeton=False, correlation=element.order_item_id))
        return r.status_code in (200, 201, 202)


class EtatServicesHttp:
    """D3 : API versionnée de slice-management-service, avec le jeton du client."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def etats(self, client_id: str, order_id: str) -> dict[str, dict]:
        r = await envoyer(self.client, "GET", "/api/slice-management/v1/service", params={"orderId": order_id},
                          headers=entetes(transmettre_jeton=True))
        if r.status_code != 200:
            raise DependanceIndisponible(f"D3 : réponse {r.status_code}")
        return {s["orderItemId"]: s for s in r.json()}
