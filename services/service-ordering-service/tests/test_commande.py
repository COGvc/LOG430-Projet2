"""UC-04 : idempotence, validations et lecture dégradée, avec des faux pour les ports."""
import asyncio

import pytest

from app.application import GestionCommandes
from app.catalog import Catalogue, ServiceSpecification
from app.domain import (CleIdempotenceReutilisee, DroneIntrouvable, LigneDemandee, TypeServiceInconnu,
                        etat_global)
from candronex_common.clienthttp import DependanceIndisponible

LIGNES = [LigneDemandee("DRN-0231", "C2"), LigneDemandee("DRN-0231", "IMAGERY")]


class FauxDepot:
    def __init__(self):
        self.commandes, self.n = {}, 1000

    async def prochain_numero(self):
        self.n += 1
        return self.n

    async def ajouter(self, c):
        cle = (c.client_id, c.idempotency_key)
        return self.commandes.setdefault(cle, c)

    async def trouver_par_cle(self, client_id, cle):
        return self.commandes.get((client_id, cle))

    async def trouver(self, client_id, order_id):
        return next((c for c in self.commandes.values() if c.order_id == order_id and c.client_id == client_id), None)


class FauxDrones:
    def __init__(self, presents=("DRN-0231",)):
        self.presents, self.appels = presents, 0

    async def existe(self, client_id, drone_id):
        self.appels += 1
        return drone_id in self.presents


class FauxEtats:
    def __init__(self, erreur=None):
        self.erreur = erreur

    async def etats(self, client_id, order_id):
        if self.erreur:
            raise self.erreur
        return {}


def gestion(drones=None, etats=None):
    cat = Catalogue([ServiceSpecification(t, t, {"sst": 1}) for t in ("C2", "IMAGERY")])
    return GestionCommandes(FauxDepot(), cat, drones or FauxDrones(), etats or FauxEtats(), asyncio.Event())


def test_rejouer_la_meme_commande_ne_cree_pas_de_doublon():
    g, drones = gestion(), FauxDrones()
    g.drones = drones
    c1, cree1 = asyncio.run(g.commander("inspectra-demo", "k-42", LIGNES))
    c2, cree2 = asyncio.run(g.commander("inspectra-demo", "k-42", list(reversed(LIGNES))))
    assert (cree1, cree2) == (True, False)
    assert c1.order_id == c2.order_id == "SO-1001"
    assert [e.order_item_id for e in c1.elements] == ["SOI-1001-1", "SOI-1001-2"]
    assert drones.appels == 1  # le rejeu ne revérifie pas le drone


def test_meme_cle_autre_contenu_refuse():
    g = gestion()
    asyncio.run(g.commander("inspectra-demo", "k-42", LIGNES))
    with pytest.raises(CleIdempotenceReutilisee):
        asyncio.run(g.commander("inspectra-demo", "k-42", LIGNES[:1]))


def test_la_cle_est_propre_a_chaque_client():
    g = gestion()
    asyncio.run(g.commander("inspectra-demo", "k-42", LIGNES))
    _, cree = asyncio.run(g.commander("aerotech-demo", "k-42", LIGNES))
    assert cree


def test_service_hors_catalogue_et_drone_inconnu_refuses():
    g = gestion(drones=FauxDrones(presents=()))
    with pytest.raises(TypeServiceInconnu):
        asyncio.run(g.commander("c", "k1", [LigneDemandee("DRN-0231", "VIDEO")]))
    with pytest.raises(DroneIntrouvable):
        asyncio.run(g.commander("c", "k2", LIGNES))


def test_lecture_degradee_si_slice_management_service_indisponible():
    g = gestion(etats=FauxEtats(DependanceIndisponible("D3")))
    c, _ = asyncio.run(g.commander("inspectra-demo", "k-42", LIGNES))
    c.elements[0].dispatch_status = "DISPATCHED"
    vue = asyncio.run(g.consulter("inspectra-demo", c.order_id))
    assert [v.state for v in vue.elements] == ["UNKNOWN", "PENDING"]
    assert vue.state == "UNKNOWN"


@pytest.mark.parametrize("etats, attendu", [
    (["PENDING", "PENDING"], "ACKNOWLEDGED"),
    (["ACTIVE", "ACTIVATING"], "IN_PROGRESS"),
    (["ACTIVE", "ACTIVE"], "COMPLETED"),
    (["ACTIVE", "FAILED"], "PARTIAL"),  # un échec n'est jamais masqué (H6)
    (["FAILED", "FAILED"], "FAILED"),
    (["ACTIVE", "UNKNOWN"], "UNKNOWN"),
])
def test_etat_global_derive(etats, attendu):
    assert etat_global(etats) == attendu
