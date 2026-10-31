"""Domaine de la commande. Python pur : aucun framework."""
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta


class CommandeInvalide(ValueError):
    pass


class TypeServiceInconnu(Exception):
    pass


class DroneIntrouvable(Exception):
    pass


class CleIdempotenceReutilisee(Exception):
    pass


class CommandeIntrouvable(Exception):
    pass


@dataclass(frozen=True)
class LigneDemandee:
    drone_id: str
    service_type: str


@dataclass
class ElementCommande:
    order_item_id: str
    drone_id: str
    service_type: str
    characteristics: dict  # figées à la commande (H5)
    dispatch_status: str = "PENDING"  # PENDING | DISPATCHED | REJECTED


@dataclass
class Commande:
    order_id: str
    client_id: str
    idempotency_key: str
    request_fingerprint: str
    accepted_at: datetime
    elements: list[ElementCommande] = field(default_factory=list)


def valider_lignes(lignes: list[LigneDemandee]) -> None:
    if not lignes:
        raise CommandeInvalide("items : au moins un élément")
    if len(set(lignes)) != len(lignes):
        raise CommandeInvalide("items : un même service est demandé deux fois pour le même drone")


def empreinte(lignes: list[LigneDemandee]) -> str:
    """Empreinte du contenu demandé : même clé + autre contenu = réutilisation interdite."""
    canon = sorted((l.drone_id, l.service_type) for l in lignes)
    return hashlib.sha256(json.dumps(canon).encode()).hexdigest()


def numero_commande(sequence: int) -> str:
    return f"SO-{sequence}"


def numero_element(order_id: str, rang: int) -> str:
    return f"SOI-{order_id.removeprefix('SO-')}-{rang}"


# --- État global, dérivé de l'état de chaque élément (H6 : jamais d'échec masqué) ---

TERMINAUX = {"ACTIVE", "FAILED"}


def etat_global(etats: list[str]) -> str:
    if "UNKNOWN" in etats:
        return "UNKNOWN"
    if all(e == "PENDING" for e in etats):
        return "ACKNOWLEDGED"
    if all(e in TERMINAUX for e in etats):
        if all(e == "ACTIVE" for e in etats):
            return "COMPLETED"
        if all(e == "FAILED" for e in etats):
            return "FAILED"
        return "PARTIAL"
    return "IN_PROGRESS"


# --- Politique de reprise de la relance (étape 4) ---

def recul(tentatives: int, base_s: float = 0.2, plafond_s: float = 5.0) -> timedelta:
    """200 ms × 2^n plafonné à 5 s, avec une part aléatoire (entre la moitié et la totalité)."""
    d = min(plafond_s, base_s * 2 ** tentatives)
    return timedelta(seconds=random.uniform(d / 2, d))
