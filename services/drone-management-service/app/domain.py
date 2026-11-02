"""Domaine de la gestion des drones. Python pur : aucun framework."""
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

_DRONE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class DonneesInvalides(ValueError):
    pass


class DroneDejaEnregistre(Exception):
    pass


class DroneIntrouvable(Exception):
    pass


@dataclass(frozen=True)
class Imsi:
    """15 chiffres. Sa représentation est masquée : un journal ne peut pas divulguer l'IMSI."""
    valeur: str

    def __post_init__(self):
        if not re.fullmatch(r"\d{15}", self.valeur):
            raise DonneesInvalides("imsi : 15 chiffres attendus")

    def __str__(self):
        return f"{self.valeur[:5]}*******{self.valeur[-3:]}"

    __repr__ = __str__


@dataclass(frozen=True)
class Iccid:
    valeur: str

    def __post_init__(self):
        if not re.fullmatch(r"\d{19,20}", self.valeur):
            raise DonneesInvalides("iccid : 19 ou 20 chiffres attendus")

    def __str__(self):
        return f"{self.valeur[:4]}***{self.valeur[-3:]}"

    __repr__ = __str__


class SimType(str, Enum):
    SIM = "SIM"
    ESIM = "ESIM"


@dataclass(frozen=True)
class IdentiteReseau:
    imsi: Imsi
    iccid: Iccid


@dataclass(frozen=True)
class Drone:
    client_id: str
    drone_id: str
    sim_type: SimType
    status: str
    registered_at: datetime

    @staticmethod
    def nouveau(client_id: str, drone_id: str, sim_type: SimType, maintenant: datetime) -> "Drone":
        if not _DRONE_ID.fullmatch(drone_id):
            raise DonneesInvalides("droneId : 1 à 64 caractères parmi A-Z a-z 0-9 . _ -")
        return Drone(client_id, drone_id, sim_type, "ACTIVE", maintenant)
