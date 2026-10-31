"""Validation du JWT par le service (en plus de la passerelle), repris du projet de démarrage.

Signature RS256 (JWKS de Keycloak, en cache), exp, iss, aud = candronex-api, présence de azp.
Le clientId est la claim azp du jeton validé, jamais l'en-tête X-Client-Id.
"""
import logging
import os

import jwt  # PyJWT
from fastapi import Header

from .contexte import authorization as authorization_ctx
from .erreurs import ErreurApi
from .journal import evenement

JWKS_URL = "http://keycloak:8080/realms/candronex/protocol/openid-connect/certs"
EMETTEUR = "http://localhost:8081/realms/candronex"
AUDIENCE = "candronex-api"


class JetonInvalide(Exception):
    pass


class VerificateurJeton:
    def __init__(self, emetteur=None, audience=None, jwks_url=None, cle=None):
        self.emetteur = emetteur or os.environ.get("JWT_ISSUER", EMETTEUR)
        self.audience = audience or os.environ.get("JWT_AUDIENCE", AUDIENCE)
        self._cle = cle  # clé publique fixe (tests); sinon, JWKS de Keycloak
        self._jwks = None if cle else jwt.PyJWKClient(jwks_url or os.environ.get("JWT_JWKS_URL", JWKS_URL))

    def verifier(self, authorization: str | None) -> dict:
        """Retourne les claims du jeton, ou lève JetonInvalide."""
        if not authorization or not authorization.startswith("Bearer "):
            raise JetonInvalide("jeton absent")
        jeton = authorization.removeprefix("Bearer ").strip()
        try:
            cle = self._cle or self._jwks.get_signing_key_from_jwt(jeton).key
            return jwt.decode(
                jeton, cle,
                algorithms=["RS256"],  # jamais « none », jamais HS256 avec la clé publique
                issuer=self.emetteur,
                audience=self.audience,
                options={"require": ["exp", "iss", "aud", "azp"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise JetonInvalide(str(e)) from e


_verificateur: VerificateurJeton | None = None


def utiliser(verificateur: VerificateurJeton) -> None:
    global _verificateur
    _verificateur = verificateur


async def client_courant(authorization: str | None = Header(default=None)) -> str:
    """Dépendance FastAPI des routes versionnées : retourne le clientId (azp) ou répond 401."""
    global _verificateur
    if _verificateur is None:
        _verificateur = VerificateurJeton()
    try:
        claims = _verificateur.verifier(authorization)
    except JetonInvalide as e:
        evenement("jeton refusé", logging.WARNING, motif=str(e))
        raise ErreurApi(401, "NON_AUTHENTIFIE", "Jeton absent, expiré ou invalide.")
    authorization_ctx.set(authorization)
    return claims["azp"]
