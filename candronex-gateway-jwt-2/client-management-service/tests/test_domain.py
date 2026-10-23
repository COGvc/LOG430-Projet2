"""Tests unitaires du domaine UC-01 : aucun Keycloak, aucun HTTP (ports remplaces par des doublures)."""
import pytest

from app.adapters import DepotEnMemoire
from app.domain import (ClientDejaExistant, DonneesInvalides, IdentifiantsAcces,
                        OnboarderClientB2B)


class FauxFournisseur:
    def __init__(self):
        self.crees: list[str] = []

    def creer_identifiants(self, client_id: str, description: str) -> IdentifiantsAcces:
        self.crees.append(client_id)
        return IdentifiantsAcces(client_id=client_id, client_secret="s3cret")


def nouveau():
    fournisseur = FauxFournisseur()
    return OnboarderClientB2B(fournisseur, DepotEnMemoire()), fournisseur


def test_onboarding_cree_le_compte_et_les_identifiants():
    onboarder, fournisseur = nouveau()
    client, ids = onboarder.executer("Inspectra Drone Services")
    assert client.client_id == "inspectra-drone-services" == ids.client_id
    assert client.statut == "ACTIF"
    assert ids.client_secret == "s3cret"
    assert fournisseur.crees == ["inspectra-drone-services"]


def test_client_deja_existant_est_refuse_sans_rappeler_le_fournisseur():
    onboarder, fournisseur = nouveau()
    onboarder.executer("Aerotech")
    with pytest.raises(ClientDejaExistant):
        onboarder.executer("  AEROTECH ")
    assert fournisseur.crees == ["aerotech"]


@pytest.mark.parametrize("nom", ["", " ", "x"])
def test_nom_invalide(nom):
    onboarder, fournisseur = nouveau()
    with pytest.raises(DonneesInvalides):
        onboarder.executer(nom)
    assert fournisseur.crees == []
