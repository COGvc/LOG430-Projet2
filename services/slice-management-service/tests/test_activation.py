"""UC-06 : le traitement d'activation, avec des faux pour l'inventaire, D4 et le réseau."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import application
from app.application import TraitementActivation
from app.domain import (CauseEchec, Etat, IdentiteIntrouvable, Imsi, ProvisioningOutcome, ServiceInstance,
                        SliceConfiguration, TransitionIllegale)
from app.reseau import MockOpen5GSAdapter
from candronex_common.clienthttp import DependanceIndisponible, Disjoncteur

C2 = {"sst": 2, "sd": "000001", "dnn": "c2", "fiveQi": 7, "arp": 2, "ambrUplink": "20 Mbps", "ambrDownlink": "20 Mbps"}
IMSI = Imsi("999700000000231")


def service(il_y_a_s: float = 0, attempts: int = 0) -> ServiceInstance:
    accepte = datetime.now(timezone.utc) - timedelta(seconds=il_y_a_s)
    return ServiceInstance("s-1", "SOI-1001-1", "SO-1001", "inspectra-demo", "DRN-0231", "C2", C2,
                           Etat.ACTIVATING, accepte, attempts=attempts)


class FauxInventaire:
    def __init__(self):
        self.enregistres, self.reportes = [], []

    async def enregistrer(self, s):
        self.enregistres.append(s)

    async def reporter(self, s, delai):
        self.reportes.append(s)


class FausseIdentite:
    def __init__(self, erreur=None):
        self.erreur, self.appels = erreur, 0

    async def resoudre(self, client_id, drone_id):
        self.appels += 1
        if self.erreur:
            raise self.erreur
        return IMSI


class FauxReseau:
    def __init__(self, issue=ProvisioningOutcome.SUCCEEDED, attente_s=0.0):
        self.issue, self.attente_s, self.configs = issue, attente_s, []

    async def subscribe(self, config):
        self.configs.append(config)
        await asyncio.sleep(self.attente_s)
        return self.issue


def activer(s, identite=None, reseau=None):
    inv = FauxInventaire()
    t = TraitementActivation(inv, identite or FausseIdentite(), reseau or FauxReseau(), asyncio.Event())
    asyncio.run(t._uc06(s))
    return inv


def test_activation_reussie_applique_les_caracteristiques_commandees():
    s, reseau = service(), FauxReseau()
    inv = activer(s, reseau=reseau)
    assert s.state is Etat.ACTIVE and s.activated_at is not None
    assert inv.enregistres == [s]
    assert reseau.configs[0] == SliceConfiguration(IMSI, 2, "000001", "c2", 7, 2, "20 Mbps", "20 Mbps")


def test_refus_du_reseau_donne_un_echec_sans_reprise():
    s = service()
    activer(s, reseau=FauxReseau(ProvisioningOutcome.REJECTED))
    assert (s.state, s.failure_cause) == (Etat.FAILED, CauseEchec.NETWORK_REJECTED)


def test_reseau_trop_lent_donne_une_issue_indeterminee(monkeypatch):
    monkeypatch.setattr(application, "DELAI_RESEAU_S", 0.05)
    s = service()
    activer(s, reseau=FauxReseau(attente_s=1))
    assert (s.state, s.failure_cause) == (Etat.FAILED, CauseEchec.NETWORK_INDETERMINATE)


def test_drone_service_indisponible_remet_en_file_sans_appeler_le_reseau():
    s, reseau = service(), FauxReseau()
    inv = activer(s, FausseIdentite(DependanceIndisponible("D4")), reseau)
    assert inv.reportes == [s] and inv.enregistres == [] and reseau.configs == []


def test_drone_service_indisponible_apres_l_echeance_donne_un_echec():
    s = service(il_y_a_s=31)
    activer(s, FausseIdentite(DependanceIndisponible("D4")))
    assert (s.state, s.failure_cause) == (Etat.FAILED, CauseEchec.IDENTITY_UNRESOLVED)


def test_drone_inconnu_donne_un_echec_d_identite():
    s = service()
    activer(s, FausseIdentite(IdentiteIntrouvable("DRN-0231")))
    assert (s.state, s.failure_cause) == (Etat.FAILED, CauseEchec.IDENTITY_UNRESOLVED)


def test_transition_illegale_refusee():
    s = service()
    s.state = Etat.PENDING
    with pytest.raises(TransitionIllegale):
        s.activer(datetime.now(timezone.utc))


def test_imsi_masque_dans_toute_representation():
    assert str(IMSI) == repr(IMSI) == "99970*******231"
    assert "999700000000231" not in repr(SliceConfiguration.depuis(IMSI, C2))


def test_mock_echec_force_a_100_pourcent():
    mock = MockOpen5GSAdapter(0, 0, taux_echec=1.0)
    assert asyncio.run(mock.subscribe(SliceConfiguration.depuis(IMSI, C2))) is ProvisioningOutcome.REJECTED


def test_mock_ecriture_conditionnelle_sans_doublon():
    mock, config = MockOpen5GSAdapter(0, 0), SliceConfiguration.depuis(IMSI, C2)
    for _ in range(3):
        assert asyncio.run(mock.subscribe(config)) is ProvisioningOutcome.SUCCEEDED
    assert len(mock._abonnements) == 1


def test_disjoncteur_s_ouvre_puis_laisse_passer_un_essai():
    d = Disjoncteur("D4", seuil=2, duree_s=0.05)

    async def panne():
        raise DependanceIndisponible("x")

    async def ok():
        return "ok"

    async def scenario():
        for _ in range(2):
            with pytest.raises(DependanceIndisponible):
                await d.appeler(panne)
        with pytest.raises(DependanceIndisponible, match="ouvert"):
            await d.appeler(ok)  # ouvert : l'appel n'a pas lieu
        await asyncio.sleep(0.06)
        assert await d.appeler(ok) == "ok"  # essai réussi : refermé
        assert d.echecs == 0

    asyncio.run(scenario())
