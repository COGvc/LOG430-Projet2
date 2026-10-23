"""Domaine de client-management-service : le compte client B2B (UC-01).

Le domaine ne connait ni HTTP, ni Keycloak, ni JWT.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import re
import uuid


class DonneesInvalides(Exception):
    pass


class ClientDejaExistant(Exception):
    pass


@dataclass(frozen=True)
class IdentifiantsAcces:
    """Informations d'acces remises UNE SEULE FOIS au client B2B."""
    client_id: str
    client_secret: str


@dataclass
class ClientB2B:
    id: str
    nom: str
    client_id: str                     # meme valeur que le client_id dans Keycloak
    statut: str = "ACTIF"
    cree_le: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --- Ports (nommes selon le besoin du domaine, pas selon la technologie) ---

class FournisseurIdentitePort(Protocol):
    def creer_identifiants(self, client_id: str, description: str) -> IdentifiantsAcces: ...


class ClientB2BRepository(Protocol):
    def existe(self, client_id: str) -> bool: ...
    def enregistrer(self, client: ClientB2B) -> None: ...


# --- Service applicatif ---

class OnboarderClientB2B:
    def __init__(self, identite: FournisseurIdentitePort, depot: ClientB2BRepository):
        self._identite = identite
        self._depot = depot

    def executer(self, nom: str) -> tuple[ClientB2B, IdentifiantsAcces]:
        if not nom or len(nom.strip()) < 2:
            raise DonneesInvalides("Le nom du client B2B est obligatoire.")
        client_id = self._slug(nom)
        if self._depot.existe(client_id):
            raise ClientDejaExistant(f"Le client {client_id} existe deja.")
        identifiants = self._identite.creer_identifiants(client_id, f"Client B2B CanDroneX : {nom}")
        client = ClientB2B(id=str(uuid.uuid4()), nom=nom.strip(), client_id=client_id)
        self._depot.enregistrer(client)
        return client, identifiants

    @staticmethod
    def _slug(nom: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", nom.strip().lower()).strip("-")
