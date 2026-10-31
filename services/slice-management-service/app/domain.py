"""Domaine de l'activation et de l'inventaire. Python pur : aucun framework."""
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

ECHEANCE_ACTIVATION = timedelta(seconds=30)  # après acceptedAt (étape 4)


class TransitionIllegale(Exception):
    pass


class IdentiteIntrouvable(Exception):
    pass


class Etat(str, Enum):
    PENDING = "PENDING"
    ACTIVATING = "ACTIVATING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"


class CauseEchec(str, Enum):
    NETWORK_REJECTED = "NETWORK_REJECTED"
    NETWORK_INDETERMINATE = "NETWORK_INDETERMINATE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


class ProvisioningOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class Imsi:
    """15 chiffres. Représentation masquée (H4); la valeur complète ne sert qu'à l'adaptateur réseau."""
    valeur: str

    def __post_init__(self):
        if not re.fullmatch(r"\d{15}", self.valeur):
            raise ValueError("imsi : 15 chiffres attendus")

    def __str__(self):
        return f"{self.valeur[:5]}*******{self.valeur[-3:]}"

    __repr__ = __str__


@dataclass(frozen=True)
class SliceConfiguration:
    """Ce qu'on écrit dans le réseau : l'abonné + les caractéristiques figées à la commande (H5)."""
    imsi: Imsi
    sst: int
    sd: str
    dnn: str
    five_qi: int
    arp: int
    ambr_uplink: str
    ambr_downlink: str

    @staticmethod
    def depuis(imsi: Imsi, c: dict) -> "SliceConfiguration":
        return SliceConfiguration(imsi, c["sst"], c["sd"], c["dnn"], c["fiveQi"], c["arp"],
                                  c["ambrUplink"], c["ambrDownlink"])

    @property
    def snssai(self) -> str:
        return f"{self.sst:02d}-{self.sd}"


@dataclass
class ServiceInstance:
    service_id: str
    order_item_id: str
    order_id: str
    client_id: str
    drone_id: str
    service_type: str
    characteristics: dict
    state: Etat
    accepted_at: datetime
    failure_cause: CauseEchec | None = None
    attempts: int = 0
    started_at: datetime | None = None
    activated_at: datetime | None = None
    failed_at: datetime | None = None

    # Les seules transitions permises (étape 1). ACTIVATING est posé par la réservation (dépôt).
    def activer(self, maintenant: datetime) -> None:
        self._exiger(Etat.ACTIVATING)
        self.state, self.activated_at = Etat.ACTIVE, maintenant

    def echouer(self, cause: CauseEchec, maintenant: datetime) -> None:
        self._exiger(Etat.PENDING, Etat.ACTIVATING)
        self.state, self.failure_cause, self.failed_at = Etat.FAILED, cause, maintenant

    def echeance_depassee(self, maintenant: datetime) -> bool:
        return maintenant > self.accepted_at + ECHEANCE_ACTIVATION

    def _exiger(self, *etats: Etat) -> None:
        if self.state not in etats:
            raise TransitionIllegale(f"{self.order_item_id} : {self.state.value}")


def recul(tentatives: int, base_s: float = 0.2, plafond_s: float = 5.0) -> timedelta:
    """200 ms × 2^n plafonné à 5 s, avec une part aléatoire."""
    d = min(plafond_s, base_s * 2 ** tentatives)
    return timedelta(seconds=random.uniform(d / 2, d))
