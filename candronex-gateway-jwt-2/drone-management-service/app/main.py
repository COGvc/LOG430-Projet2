"""Service Drone minimal : montre comment un microservice utilise l'identite du client.

L'API Gateway a deja valide le JWT et le transmet tel quel (en-tete Authorization).
Le service le revalide (signature, expiration, emetteur, audience candronex-api)
et prend l'identite du client dans la claim azp du jeton verifie.
Le service n'est pas expose hors du reseau Docker.
"""
import json
import logging
import uuid
from flask import Flask, request, jsonify

from .jeton import VerificateurJeton, JetonInvalide
from . import correlation


class JsonFormatter(logging.Formatter):
    def format(self, r):
        return json.dumps({"ts": self.formatTime(r), "service": "drone-management-service",
                           "niveau": r.levelname, "message": r.getMessage(),
                           "correlationId": correlation.courant(),
                           **getattr(r, "extra_fields", {})})


h = logging.StreamHandler(); h.setFormatter(JsonFormatter())
log = logging.getLogger("drone-management-service"); log.addHandler(h); log.setLevel(logging.INFO)

app = Flask(__name__)
correlation.installer(app)
verificateur = VerificateurJeton()
DRONES: dict[str, dict] = {}   # a remplacer par la persistance de votre Phase 1


def client_courant():
    """client_id (claim azp) du jeton verifie, ou None si le jeton est refuse."""
    try:
        return verificateur.verifier(request.headers.get("Authorization"))["azp"]
    except JetonInvalide as e:
        # Ne devrait jamais arriver derriere la passerelle.
        log.warning("jeton refuse", extra={"extra_fields": {"raison": str(e)}})
        return None


@app.post("/api/drone-management/v1/drone")
def enregistrer_drone():
    cid = client_courant()
    if not cid:
        return jsonify(erreur="NON_AUTHENTIFIE"), 401
    corps = request.get_json(silent=True) or {}
    if not corps.get("imsi"):
        return jsonify(erreur="DONNEES_INVALIDES", message="imsi obligatoire"), 400
    drone = {"droneId": str(uuid.uuid4()), "imsi": corps["imsi"], "modele": corps.get("modele"),
             "proprietaire": cid}
    DRONES[drone["droneId"]] = drone
    log.info("drone enregistre", extra={"extra_fields": {"clientId": cid, "droneId": drone["droneId"]}})
    return jsonify(drone), 201


@app.get("/api/drone-management/v1/drone")
def lister_drones():
    cid = client_courant()
    if not cid:
        return jsonify(erreur="NON_AUTHENTIFIE"), 401
    return jsonify([d for d in DRONES.values() if d["proprietaire"] == cid])


@app.get("/api/drone-management/v1/drone/<drone_id>")
def consulter_drone(drone_id):
    cid = client_courant()
    if not cid:
        return jsonify(erreur="NON_AUTHENTIFIE"), 401
    d = DRONES.get(drone_id)
    # 404 (et non 403) si le drone appartient a un autre client : on ne revele pas son existence.
    if not d or d["proprietaire"] != cid:
        return jsonify(erreur="INTROUVABLE"), 404
    return jsonify(d)


@app.get("/health")
def health():
    return {"statut": "UP"}
