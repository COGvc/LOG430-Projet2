"""Appels entre services : en-têtes propagés, délais, erreurs techniques, disjoncteur."""
import logging
import time

import httpx

from .contexte import authorization, correlation_id
from .journal import evenement


class DependanceIndisponible(Exception):
    """Erreur technique : délai dépassé, connexion refusée, 5xx, ou disjoncteur ouvert."""


def creer_client(base_url: str, delai_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=delai_s,
        limits=httpx.Limits(max_connections=500, max_keepalive_connections=200),
    )


def entetes(transmettre_jeton: bool, correlation: str | None = None) -> dict:
    """D1/D3 transmettent le jeton du client; D2/D4 (arrière-plan) n'en ont pas."""
    h = {"X-Correlation-Id": correlation or correlation_id.get() or ""}
    if transmettre_jeton and authorization.get():
        h["Authorization"] = authorization.get()
    return h


async def envoyer(client: httpx.AsyncClient, methode: str, url: str, **kwargs) -> httpx.Response:
    """Traduit toute défaillance technique en DependanceIndisponible; laisse passer 2xx/4xx."""
    try:
        r = await client.request(methode, url, **kwargs)
    except httpx.HTTPError as e:
        raise DependanceIndisponible(f"{type(e).__name__} sur {url}") from e
    if r.status_code >= 500:
        raise DependanceIndisponible(f"{r.status_code} sur {url}")
    return r


class Disjoncteur:
    """Ouvert après `seuil` échecs techniques consécutifs, pendant `duree_s`, puis un seul appel d'essai.

    Seules les DependanceIndisponible comptent; une réponse métier (même 404) referme le disjoncteur.
    """

    def __init__(self, nom: str, seuil: int = 5, duree_s: float = 5.0):
        self.nom, self.seuil, self.duree_s = nom, seuil, duree_s
        self.echecs = 0
        self.ouvert_jusqua = 0.0
        self.essai_en_cours = False

    async def appeler(self, fn, *args, **kwargs):
        if self.echecs >= self.seuil:
            if time.monotonic() < self.ouvert_jusqua or self.essai_en_cours:
                raise DependanceIndisponible(f"disjoncteur {self.nom} ouvert")
            self.essai_en_cours = True  # semi-ouvert
        try:
            resultat = await fn(*args, **kwargs)
        except DependanceIndisponible:
            self.echecs += 1
            if self.echecs >= self.seuil:
                self.ouvert_jusqua = time.monotonic() + self.duree_s
                if self.echecs == self.seuil:
                    evenement("disjoncteur ouvert", logging.WARNING, disjoncteur=self.nom)
            raise
        except Exception:
            self._fermer()
            raise
        finally:
            self.essai_en_cours = False
        self._fermer()
        return resultat

    def _fermer(self):
        if self.echecs >= self.seuil:
            evenement("disjoncteur fermé", disjoncteur=self.nom)
        self.echecs = 0
