"""NetworkProvisioningPort et son adaptateur simulé, MockOpen5GSAdapter.

En Phase 3, un Open5GSAdapter réalisera le même port; seul le point d'assemblage changera.
"""
import asyncio
import random
from typing import Protocol

from candronex_common.journal import evenement

from .domain import ProvisioningOutcome, SliceConfiguration


class NetworkProvisioningPort(Protocol):
    async def subscribe(self, config: SliceConfiguration) -> ProvisioningOutcome:
        """Abonne l'IMSI à la tranche (S-NSSAI, DNN, QoS). Retourne SUCCEEDED ou REJECTED,
        ou lève une exception technique. Le délai d'attente est appliqué par l'application."""
        ...


class MockOpen5GSAdapter:
    """Aucune connexion réseau : latence et taux d'échec configurables, profils d'abonnés en mémoire.

    Écriture conditionnelle sur (IMSI, S-NSSAI, DNN) : une configuration déjà appliquée n'est pas réécrite
    (troisième niveau d'idempotence).
    """

    def __init__(self, latence_min_ms: int = 300, latence_max_ms: int = 1500, taux_echec: float = 0.0):
        self.latence_min_ms, self.latence_max_ms, self.taux_echec = latence_min_ms, latence_max_ms, taux_echec
        self._abonnements: dict[tuple[str, str, str], SliceConfiguration] = {}

    async def subscribe(self, config: SliceConfiguration) -> ProvisioningOutcome:
        await asyncio.sleep(random.uniform(self.latence_min_ms, self.latence_max_ms) / 1000)
        if random.random() < self.taux_echec:
            evenement("réseau simulé : refus", imsi=str(config.imsi), snssai=config.snssai, dnn=config.dnn)
            return ProvisioningOutcome.REJECTED
        cle = (config.imsi.valeur, config.snssai, config.dnn)
        if self._abonnements.get(cle) == config:
            evenement("réseau simulé : déjà configuré", imsi=str(config.imsi), snssai=config.snssai, dnn=config.dnn)
        else:
            self._abonnements[cle] = config
        return ProvisioningOutcome.SUCCEEDED
