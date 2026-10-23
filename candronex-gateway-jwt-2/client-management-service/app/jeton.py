"""Verification du JWT par le microservice (defense en profondeur).

La passerelle a deja valide le jeton. Le service le revalide quand meme :
signature (JWKS de Keycloak), expiration, emetteur et audience candronex-api.
Un appel qui contourne la passerelle (erreur de configuration, reseau interne
compromis) ou un jeton emis pour une autre API est ainsi refuse.

Meme fichier dans client-management-service/ et drone-management-service/ : chaque service a son
propre contexte de build Docker.
"""
import os

import jwt  # PyJWT

JWKS_URL = "http://keycloak:8080/realms/candronex/protocol/openid-connect/certs"
EMETTEUR = "http://localhost:8081/realms/candronex"
AUDIENCE = "candronex-api"


class JetonInvalide(Exception):
    pass


class VerificateurJeton:
    def __init__(self, emetteur=None, audience=None, jwks_url=None, cle=None):
        self.emetteur = emetteur or os.environ.get("JWT_ISSUER", EMETTEUR)
        self.audience = audience or os.environ.get("JWT_AUDIENCE", AUDIENCE)
        self._cle = cle   # cle publique fixe (tests); sinon, JWKS de Keycloak
        self._jwks = None if cle else jwt.PyJWKClient(jwks_url or os.environ.get("JWT_JWKS_URL", JWKS_URL))

    def verifier(self, authorization: str | None) -> dict:
        """Retourne les claims du jeton, ou leve JetonInvalide."""
        if not authorization or not authorization.startswith("Bearer "):
            raise JetonInvalide("jeton absent")
        jeton = authorization.removeprefix("Bearer ").strip()
        try:
            cle = self._cle or self._jwks.get_signing_key_from_jwt(jeton).key
            return jwt.decode(
                jeton, cle,
                algorithms=["RS256"],          # jamais « none », jamais HS256 avec la cle publique
                issuer=self.emetteur,
                audience=self.audience,        # aud doit contenir candronex-api
                options={"require": ["exp", "iss", "aud", "azp"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise JetonInvalide(str(e)) from e
