"""Adaptateurs de sortie : Keycloak (fournisseur d'identite) et depot en memoire."""
import logging
import os
import requests

from .domain import IdentifiantsAcces, ClientB2B, ClientDejaExistant

log = logging.getLogger("client-management-service")


class KeycloakAdapter:
    """Implemente FournisseurIdentitePort avec l'API d'administration de Keycloak.

    Le service s'authentifie lui-meme par client credentials
    (compte technique 'client-management-service', role realm-management/manage-clients).
    """

    def __init__(self):
        self.base = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
        self.realm = os.environ.get("KEYCLOAK_REALM", "candronex")
        self.client_id = os.environ.get("CLIENT_MANAGEMENT_SERVICE_CLIENT_ID", "client-management-service")
        self.secret = os.environ["CLIENT_MANAGEMENT_SERVICE_SECRET"]

    def _jeton_admin(self) -> str:
        r = requests.post(
            f"{self.base}/realms/{self.realm}/protocol/openid-connect/token",
            data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.secret},
            timeout=5,
        )
        r.raise_for_status()
        return r.json()["access_token"]

    def creer_identifiants(self, client_id: str, description: str) -> IdentifiantsAcces:
        h = {"Authorization": f"Bearer {self._jeton_admin()}"}
        admin = f"{self.base}/admin/realms/{self.realm}/clients"
        corps = {
            "clientId": client_id,
            "description": description,
            "enabled": True,
            "publicClient": False,
            "serviceAccountsEnabled": True,   # flux client credentials
            "standardFlowEnabled": False,     # pas d'utilisateur, pas de navigateur
            "directAccessGrantsEnabled": False,
        }
        r = requests.post(admin, json=corps, headers=h, timeout=5)
        # Le depot en memoire est vide apres un redemarrage, mais Keycloak se souvient du client :
        # son 409 est la source de verite pour « client deja existant ».
        if r.status_code == 409:
            raise ClientDejaExistant(f"Le client {client_id} existe deja.")
        r.raise_for_status()
        uuid_kc = r.headers["Location"].rsplit("/", 1)[-1]
        s = requests.get(f"{admin}/{uuid_kc}/client-secret", headers=h, timeout=5)
        s.raise_for_status()
        return IdentifiantsAcces(client_id=client_id, client_secret=s.json()["value"])


class DepotEnMemoire:
    """Depot minimal pour le projet de demarrage (a remplacer par une vraie base)."""

    def __init__(self):
        self._clients: dict[str, ClientB2B] = {}

    def existe(self, client_id: str) -> bool:
        return client_id in self._clients

    def enregistrer(self, client: ClientB2B) -> None:
        self._clients[client.client_id] = client
