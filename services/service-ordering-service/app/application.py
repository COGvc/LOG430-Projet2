"""Cas d'utilisation et ports de sortie de la commande. Ne dépend que du domaine et du module catalogue."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from candronex_common.clienthttp import DependanceIndisponible
from candronex_common.journal import evenement

from .catalog import Catalogue
from .domain import (CleIdempotenceReutilisee, Commande, CommandeIntrouvable, DroneIntrouvable, ElementCommande,
                     LigneDemandee, TypeServiceInconnu, empreinte, etat_global, numero_commande, numero_element,
                     recul, valider_lignes)


class DepotCommandes(Protocol):
    async def prochain_numero(self) -> int: ...
    async def ajouter(self, commande: Commande) -> Commande: ...  # retourne l'existante si (client, clé) déjà prise
    async def trouver_par_cle(self, client_id: str, cle: str) -> Commande | None: ...
    async def trouver(self, client_id: str, order_id: str) -> Commande | None: ...
    async def lister(self, client_id: str, limite: int) -> list[Commande]: ...
    async def reserver_a_transmettre(self, limite: int) -> list[tuple[Commande, ElementCommande, int]]: ...
    async def marquer_transmis(self, order_item_id: str) -> None: ...
    async def marquer_rejete(self, order_item_id: str) -> None: ...
    async def reporter(self, order_item_id: str, tentatives: int, delai) -> None: ...


class EligibiliteDrone(Protocol):  # D1
    async def existe(self, client_id: str, drone_id: str) -> bool: ...  # lève DependanceIndisponible


class DemandeActivation(Protocol):  # D2
    async def demander(self, commande: Commande, element: ElementCommande) -> bool: ...  # False = refus définitif


class EtatServices(Protocol):  # D3
    async def etats(self, client_id: str, order_id: str) -> dict[str, dict]: ...  # orderItemId -> service


@dataclass
class VueElement:
    element: ElementCommande
    state: str
    service: dict | None


@dataclass
class VueCommande:
    commande: Commande
    state: str
    elements: list[VueElement]


class GestionCommandes:
    def __init__(self, depot: DepotCommandes, catalogue: Catalogue, drones: EligibiliteDrone,
                 etat_services: EtatServices, nouvelle_commande: asyncio.Event):
        self.depot, self.catalogue, self.drones, self.etat_services = depot, catalogue, drones, etat_services
        self.nouvelle_commande = nouvelle_commande  # réveille la relance (I8)

    async def commander(self, client_id: str, cle: str, lignes: list[LigneDemandee]) -> tuple[Commande, bool]:
        """UC-04. Retourne (commande, créée?). Aucun appel à slice-management-service."""
        valider_lignes(lignes)
        fp = empreinte(lignes)
        existante = await self.depot.trouver_par_cle(client_id, cle)
        if existante:
            return self._rejeu(existante, fp), False

        specs = {}
        for l in lignes:
            spec = self.catalogue.specification(l.service_type)
            if spec is None:
                raise TypeServiceInconnu(l.service_type)
            specs[l.service_type] = spec
        drones = sorted({l.drone_id for l in lignes})
        presents = await asyncio.gather(*(self.drones.existe(client_id, d) for d in drones))  # D1
        for d, ok in zip(drones, presents):
            if not ok:
                raise DroneIntrouvable(d)

        order_id = numero_commande(await self.depot.prochain_numero())
        commande = Commande(order_id, client_id, cle, fp, datetime.now(timezone.utc), [
            ElementCommande(numero_element(order_id, i), l.drone_id, l.service_type, specs[l.service_type].characteristics)
            for i, l in enumerate(lignes, start=1)
        ])
        enregistree = await self.depot.ajouter(commande)
        if enregistree.order_id != order_id:  # même clé soumise en parallèle : l'autre a gagné
            return self._rejeu(enregistree, fp), False
        evenement("commande acceptée", clientId=client_id, orderId=order_id,
                  elements=[e.order_item_id for e in commande.elements])
        self.nouvelle_commande.set()
        return commande, True

    @staticmethod
    def _rejeu(existante: Commande, fp: str) -> Commande:
        if existante.request_fingerprint != fp:
            raise CleIdempotenceReutilisee(existante.idempotency_key)
        evenement("commande rejouée", orderId=existante.order_id)
        return existante

    async def consulter(self, client_id: str, order_id: str) -> VueCommande:
        commande = await self.depot.trouver(client_id, order_id)
        if commande is None:
            raise CommandeIntrouvable(order_id)
        services: dict[str, dict] | None = {}
        if any(e.dispatch_status != "PENDING" for e in commande.elements):
            try:
                services = await self.etat_services.etats(client_id, order_id)  # D3
            except DependanceIndisponible as e:
                evenement("lecture dégradée", logging.WARNING, orderId=order_id, cause=str(e))
                services = None
        vues = []
        for el in commande.elements:
            if el.dispatch_status == "PENDING":
                vues.append(VueElement(el, "PENDING", None))
            elif services is None:
                vues.append(VueElement(el, "UNKNOWN", None))
            elif el.dispatch_status == "REJECTED":
                vues.append(VueElement(el, "FAILED", None))
            else:
                s = services.get(el.order_item_id)
                vues.append(VueElement(el, s["state"] if s else "PENDING", s))
        return VueCommande(commande, etat_global([v.state for v in vues]), vues)

    async def lister(self, client_id: str, limite: int) -> list[Commande]:
        return await self.depot.lister(client_id, limite)


class Relance:
    """Transmet les éléments PENDING à slice-management-service (D2), hors de toute requête du client."""

    def __init__(self, depot: DepotCommandes, activation: DemandeActivation, reveil: asyncio.Event,
                 lot: int = 200, sondage_s: float = 0.2):
        self.depot, self.activation, self.reveil = depot, activation, reveil
        self.lot, self.sondage_s = lot, sondage_s

    async def tourner(self) -> None:
        while True:
            try:
                reserves = await self.depot.reserver_a_transmettre(self.lot)
                if reserves:
                    await asyncio.gather(*(self._transmettre(c, e, n) for c, e, n in reserves))
                    continue  # il en reste peut-être
            except Exception as e:  # la boucle ne doit jamais mourir
                evenement("relance : erreur", logging.ERROR, cause=repr(e))
            self.reveil.clear()
            try:
                await asyncio.wait_for(self.reveil.wait(), self.sondage_s)
            except asyncio.TimeoutError:
                pass

    async def _transmettre(self, commande: Commande, element: ElementCommande, tentatives: int) -> None:
        try:
            accepte = await self.activation.demander(commande, element)
        except DependanceIndisponible as e:
            await self.depot.reporter(element.order_item_id, tentatives + 1, recul(tentatives))
            evenement("transmission reportée", logging.WARNING, orderId=commande.order_id,
                      orderItemId=element.order_item_id, tentatives=tentatives + 1, cause=str(e))
            return
        if accepte:
            await self.depot.marquer_transmis(element.order_item_id)
            evenement("élément transmis", orderId=commande.order_id, orderItemId=element.order_item_id)
        else:
            await self.depot.marquer_rejete(element.order_item_id)
            evenement("élément refusé par slice-management-service", logging.ERROR,
                      orderId=commande.order_id, orderItemId=element.order_item_id)
