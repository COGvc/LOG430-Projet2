"""Cas d'utilisation, ports de sortie et traitement d'activation (UC-06)."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from candronex_common.clienthttp import DependanceIndisponible
from candronex_common.contexte import correlation_id
from candronex_common.journal import evenement

from .domain import (CauseEchec, Etat, IdentiteIntrouvable, Imsi, ProvisioningOutcome, ServiceInstance,
                     SliceConfiguration, recul)
from .reseau import NetworkProvisioningPort

DELAI_RESEAU_S = 3.0  # politique de l'application, pas de l'adaptateur


class InventaireServices(Protocol):
    async def ajouter_si_absent(self, s: ServiceInstance) -> tuple[ServiceInstance, bool]: ...
    async def reserver_echus(self, limite: int) -> list[ServiceInstance]: ...  # → ACTIVATING, avec bail
    async def enregistrer(self, s: ServiceInstance) -> None: ...
    async def reporter(self, s: ServiceInstance, delai: timedelta) -> None: ...  # → PENDING, attempts + 1
    async def par_commande(self, client_id: str, order_id: str) -> list[ServiceInstance]: ...
    async def par_drone(self, client_id: str, drone_id: str) -> list[ServiceInstance]: ...


class IdentiteReseauDrone(Protocol):  # D4
    async def resoudre(self, client_id: str, drone_id: str) -> Imsi: ...
    # lève IdentiteIntrouvable (définitif) ou DependanceIndisponible (technique)


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


class GestionServices:
    def __init__(self, inventaire: InventaireServices, reveil: asyncio.Event):
        self.inventaire, self.reveil = inventaire, reveil

    async def demander_activation(self, order_item_id: str, order_id: str, client_id: str, drone_id: str,
                                  service_type: str, characteristics: dict, accepted_at: datetime):
        """D2. Idempotent sur orderItemId : une seconde demande retourne le service existant."""
        s = ServiceInstance(str(uuid.uuid4()), order_item_id, order_id, client_id, drone_id, service_type,
                            characteristics, Etat.PENDING, accepted_at)
        s, cree = await self.inventaire.ajouter_si_absent(s)
        if cree:
            evenement("activation demandée", orderId=order_id, orderItemId=order_item_id, serviceType=service_type)
            self.reveil.set()
        return s, cree

    async def services(self, client_id: str, order_id: str | None, drone_id: str | None):
        if order_id:
            return await self.inventaire.par_commande(client_id, order_id)
        return await self.inventaire.par_drone(client_id, drone_id)


class TraitementActivation:
    """Prend les services échus et applique UC-06, jusqu'à `concurrence` activations simultanées."""

    def __init__(self, inventaire: InventaireServices, identite: IdentiteReseauDrone,
                 reseau: NetworkProvisioningPort, reveil: asyncio.Event, concurrence: int = 500,
                 sondage_s: float = 0.1):
        self.inventaire, self.identite, self.reseau, self.reveil = inventaire, identite, reseau, reveil
        self.concurrence, self.sondage_s = concurrence, sondage_s
        self.en_cours = 0

    async def tourner(self) -> None:
        while True:
            try:
                libres = self.concurrence - self.en_cours
                if libres > 0:
                    lot = await self.inventaire.reserver_echus(min(libres, 200))
                    for s in lot:
                        self.en_cours += 1
                        asyncio.create_task(self._activer(s))
                    if lot:
                        continue
            except Exception as e:  # la boucle ne doit jamais mourir
                evenement("activation : erreur de la boucle", logging.ERROR, cause=repr(e))
            self.reveil.clear()
            try:
                await asyncio.wait_for(self.reveil.wait(), self.sondage_s)
            except asyncio.TimeoutError:
                pass

    async def _activer(self, s: ServiceInstance) -> None:
        correlation_id.set(s.order_item_id)  # propre à cette tâche
        try:
            await self._uc06(s)
        except Exception as e:
            evenement("activation : erreur imprévue", logging.ERROR, orderItemId=s.order_item_id, cause=repr(e))
        finally:
            self.en_cours -= 1
            self.reveil.set()  # une place s'est libérée

    async def _uc06(self, s: ServiceInstance) -> None:
        ids = {"orderId": s.order_id, "orderItemId": s.order_item_id}
        if s.attempts > 0 and s.echeance_depassee(maintenant()):
            return await self._echec(s, CauseEchec.IDENTITY_UNRESOLVED, ids)
        try:
            imsi = await self.identite.resoudre(s.client_id, s.drone_id)  # D4
        except IdentiteIntrouvable:
            return await self._echec(s, CauseEchec.IDENTITY_UNRESOLVED, ids)
        except DependanceIndisponible as e:
            if s.echeance_depassee(maintenant()):
                return await self._echec(s, CauseEchec.IDENTITY_UNRESOLVED, ids)
            await self.inventaire.reporter(s, recul(s.attempts))
            evenement("activation reportée", logging.WARNING, tentatives=s.attempts + 1, cause=str(e), **ids)
            return

        config = SliceConfiguration.depuis(imsi, s.characteristics)
        evenement("appel NetworkProvisioningPort", snssai=config.snssai, dnn=config.dnn, imsi=str(imsi), **ids)
        try:
            issue = await asyncio.wait_for(self.reseau.subscribe(config), DELAI_RESEAU_S)
        except asyncio.TimeoutError:
            issue = ProvisioningOutcome.INDETERMINATE
        except Exception as e:
            evenement("réseau : erreur technique", logging.WARNING, cause=repr(e), **ids)
            issue = ProvisioningOutcome.INDETERMINATE

        if issue is ProvisioningOutcome.SUCCEEDED:
            s.activer(maintenant())
            await self.inventaire.enregistrer(s)
            evenement("activation réussie", delaiMs=int((s.activated_at - s.accepted_at).total_seconds() * 1000), **ids)
        elif issue is ProvisioningOutcome.REJECTED:
            await self._echec(s, CauseEchec.NETWORK_REJECTED, ids)
        else:  # pas de reprise aveugle : l'état réel du réseau est inconnu (UC-10)
            await self._echec(s, CauseEchec.NETWORK_INDETERMINATE, ids)

    async def _echec(self, s: ServiceInstance, cause: CauseEchec, ids: dict) -> None:
        s.echouer(cause, maintenant())
        await self.inventaire.enregistrer(s)
        evenement("activation en échec", logging.WARNING, cause=cause.value, **ids)
