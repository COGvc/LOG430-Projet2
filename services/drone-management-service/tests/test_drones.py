"""UC-02 : règles de forme et non-divulgation de l'IMSI."""
import asyncio

import pytest

from app.application import GestionDrones
from app.domain import DonneesInvalides, DroneIntrouvable, Iccid, Imsi


class FauxDepot:
    def __init__(self):
        self.drones, self.identites = {}, {}

    async def ajouter(self, drone, identite):
        self.drones[(drone.client_id, drone.drone_id)] = drone
        self.identites[(drone.client_id, drone.drone_id)] = identite

    async def trouver(self, client_id, drone_id):
        return self.drones.get((client_id, drone_id))

    async def trouver_identite(self, client_id, drone_id):
        return self.identites.get((client_id, drone_id))


@pytest.mark.parametrize("imsi", ["", "12345", "99970000000023a", "9997000000002310"])
def test_imsi_invalide(imsi):
    with pytest.raises(DonneesInvalides):
        Imsi(imsi)


def test_iccid_invalide():
    with pytest.raises(DonneesInvalides):
        Iccid("123")


def test_drone_id_invalide():
    with pytest.raises(DonneesInvalides):
        asyncio.run(GestionDrones(FauxDepot()).enregistrer("c", "DRN 0231 !", "999700000000231",
                                                           "8930272000000000023", "ESIM"))


def test_le_drone_ne_porte_pas_l_imsi_et_l_identite_est_masquee():
    g = GestionDrones(FauxDepot())
    drone = asyncio.run(g.enregistrer("inspectra-demo", "DRN-0231", "999700000000231", "8930272000000000023", "SIM"))
    assert "999700000000231" not in repr(drone)
    imsi = asyncio.run(g.resoudre_identite("inspectra-demo", "DRN-0231"))
    assert imsi.valeur == "999700000000231" and str(imsi) == "99970*******231"


def test_un_client_ne_voit_pas_le_drone_d_un_autre():
    g = GestionDrones(FauxDepot())
    asyncio.run(g.enregistrer("inspectra-demo", "DRN-0231", "999700000000231", "8930272000000000023", "SIM"))
    with pytest.raises(DroneIntrouvable):
        asyncio.run(g.consulter("aerotech-demo", "DRN-0231"))
    with pytest.raises(DroneIntrouvable):
        asyncio.run(g.resoudre_identite("aerotech-demo", "DRN-0231"))
