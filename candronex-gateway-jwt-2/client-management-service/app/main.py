"""Adaptateur d'entree REST de client-management-service."""
import json
import logging
from flask import Flask, request, jsonify

from .domain import OnboarderClientB2B, DonneesInvalides, ClientDejaExistant
from .adapters import KeycloakAdapter, DepotEnMemoire
from .jeton import VerificateurJeton, JetonInvalide
from . import correlation


class JsonFormatter(logging.Formatter):
    def format(self, r):
        return json.dumps({"ts": self.formatTime(r), "service": "client-management-service",
                           "niveau": r.levelname, "message": r.getMessage(),
                           "correlationId": correlation.courant(),
                           **getattr(r, "extra_fields", {})})


h = logging.StreamHandler(); h.setFormatter(JsonFormatter())
log = logging.getLogger("client-management-service"); log.addHandler(h); log.setLevel(logging.INFO)

app = Flask(__name__)
correlation.installer(app)
onboarder = OnboarderClientB2B(KeycloakAdapter(), DepotEnMemoire())
verificateur = VerificateurJeton()


@app.post("/api/client-management/v1/client")
def onboarder_client():
    # La passerelle a deja verifie le jeton et la portee; le service revalide (defense en profondeur).
    try:
        claims = verificateur.verifier(request.headers.get("Authorization"))
    except JetonInvalide as e:
        log.warning("jeton refuse", extra={"extra_fields": {"raison": str(e)}})
        return jsonify(erreur="NON_AUTHENTIFIE"), 401
    if "clients:create" not in claims.get("scope", "").split():
        return jsonify(erreur="INTERDIT", message="portee clients:create requise"), 403
    appelant = claims["azp"]
    nom = (request.get_json(silent=True) or {}).get("nom", "")
    try:
        client, ids = onboarder.executer(nom)
    except DonneesInvalides as e:
        return jsonify(erreur="DONNEES_INVALIDES", message=str(e)), 400
    except ClientDejaExistant as e:
        return jsonify(erreur="CONFLIT", message=str(e)), 409
    # Ne JAMAIS journaliser le secret.
    log.info("client B2B onboarde", extra={"extra_fields": {"clientId": client.client_id, "par": appelant}})
    return jsonify(id=client.id, nom=client.nom, clientId=ids.client_id, clientSecret=ids.client_secret,
                   avertissement="Conservez ce secret : il ne sera plus jamais affiche."), 201


@app.get("/health")
def health():
    return {"statut": "UP"}
