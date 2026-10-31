"""Cas d'utilisation et port de sortie. Ne dépend que du domaine."""
from datetime import datetime, timezone
from typing import Protocol

from .domain import Drone, DroneIntrouvable, IdentiteReseau, Iccid, Imsi, SimType


class DepotDrones(Protocol):
    async def ajouter(self, drone: Drone, identite: IdentiteReseau) -> None: ...  # lève DroneDejaEnregistre
    async def trouver(self, client_id: str, drone_id: str) -> Drone | None: ...
    async def lister(self, client_id: str, limite: int) -> list[Drone]: ...
    async def trouver_identite(self, client_id: str, drone_id: str) -> IdentiteReseau | None: ...


class GestionDrones:
    def __init__(self, depot: DepotDrones):
        self.depot = depot

    async def enregistrer(self, client_id: str, drone_id: str, imsi: str, iccid: str, sim_type: str) -> Drone:
        """UC-02. Toutes les règles de forme sont vérifiées avant d'écrire; l'unicité, par la base."""
        identite = IdentiteReseau(Imsi(imsi), Iccid(iccid))
        drone = Drone.nouveau(client_id, drone_id, SimType(sim_type), datetime.now(timezone.utc))
        await self.depot.ajouter(drone, identite)
        return drone

    async def consulter(self, client_id: str, drone_id: str) -> Drone:
        drone = await self.depot.trouver(client_id, drone_id)
        if drone is None:  # inexistant ou d'un autre client : même réponse
            raise DroneIntrouvable(drone_id)
        return drone

    async def lister(self, client_id: str, limite: int) -> list[Drone]:
        return await self.depot.lister(client_id, limite)

    async def resoudre_identite(self, client_id: str, drone_id: str) -> Imsi:
        """D4 : seule sortie de l'IMSI hors de ce service."""
        identite = await self.depot.trouver_identite(client_id, drone_id)
        if identite is None:
            raise DroneIntrouvable(drone_id)
        return identite.imsi
