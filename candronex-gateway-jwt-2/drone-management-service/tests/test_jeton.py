"""Tests unitaires de la verification du JWT dans le microservice : aucun Keycloak.

Une paire de cles RSA est generee pour le test; elle joue le role de Keycloak.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.jeton import AUDIENCE, EMETTEUR, JetonInvalide, VerificateurJeton

CLE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
AUTRE_CLE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jeton(cle=CLE, **claims):
    c = {"iss": EMETTEUR, "aud": ["candronex-api"], "azp": "inspectra-demo",
         "exp": int(time.time()) + 300, "scope": "profile"}
    c.update(claims)
    c = {k: v for k, v in c.items() if v is not None}
    return "Bearer " + jwt.encode(c, cle, algorithm="RS256")


@pytest.fixture
def verificateur():
    return VerificateurJeton(cle=CLE.public_key())


def test_jeton_valide_avec_audience(verificateur):
    assert verificateur.verifier(jeton())["azp"] == "inspectra-demo"


def test_audience_en_chaine_simple(verificateur):
    assert verificateur.verifier(jeton(aud=AUDIENCE))["azp"] == "inspectra-demo"


@pytest.mark.parametrize("aud", [None, "account", ["account", "autre-api"]])
def test_audience_absente_ou_autre_refusee(verificateur, aud):
    with pytest.raises(JetonInvalide):
        verificateur.verifier(jeton(aud=aud))


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 10},                          # expire
    {"iss": "http://localhost:8081/realms/master"},         # autre emetteur
    {"azp": None},                                           # pas d'identite client
])
def test_claims_invalides_refusees(verificateur, claims):
    with pytest.raises(JetonInvalide):
        verificateur.verifier(jeton(**claims))


def test_signature_d_une_autre_cle_refusee(verificateur):
    with pytest.raises(JetonInvalide):
        verificateur.verifier(jeton(cle=AUTRE_CLE))


def test_jeton_non_signe_refuse(verificateur):
    non_signe = jwt.encode({"iss": EMETTEUR, "aud": AUDIENCE, "azp": "x", "exp": int(time.time()) + 60},
                           None, algorithm="none")
    with pytest.raises(JetonInvalide):
        verificateur.verifier("Bearer " + non_signe)


@pytest.mark.parametrize("entete", [None, "", "Basic abc", "Bearer abc.def.ghi"])
def test_entete_absent_ou_illisible(verificateur, entete):
    with pytest.raises(JetonInvalide):
        verificateur.verifier(entete)


def test_route_refuse_jeton_sans_audience(monkeypatch, verificateur):
    from app import main
    monkeypatch.setattr(main, "verificateur", verificateur)
    client = main.app.test_client()
    assert client.get("/api/drone-management/v1/drone", headers={"Authorization": jeton()}).status_code == 200
    assert client.get("/api/drone-management/v1/drone", headers={"Authorization": jeton(aud="account")}).status_code == 401
    # Un X-Client-Id forge ne remplace pas le jeton.
    assert client.get("/api/drone-management/v1/drone", headers={"X-Client-Id": "inspectra-demo"}).status_code == 401


def test_correlation_reprise_ou_generee(monkeypatch, verificateur):
    from app import main
    monkeypatch.setattr(main, "verificateur", verificateur)
    client = main.app.test_client()
    r = client.get("/api/drone-management/v1/drone", headers={"Authorization": jeton(), "X-Correlation-Id": "cmd-42"})
    assert r.headers["X-Correlation-Id"] == "cmd-42"
    genere = client.get("/api/drone-management/v1/drone", headers={"Authorization": jeton()}).headers["X-Correlation-Id"]
    assert len(genere) == 36
    # Valeur dangereuse pour les journaux : remplacee.
    r = client.get("/health", headers={"X-Correlation-Id": "a b\" faux=1"})
    assert r.headers["X-Correlation-Id"] != "a b\" faux=1"
