"""Identifiant de correlation (X-Correlation-Id).

Le client peut l'envoyer (ex. son numero de commande); la passerelle le transmet tel quel
(input_headers). Le service le reprend, ou en genere un s'il est absent ou invalide,
l'ajoute a chaque ligne de journal et le renvoie dans la reponse (la passerelle, en no-op,
recopie les en-tetes du service). S'il appelle un autre service, il le transmet dans le meme en-tete.

Meme fichier dans client-management-service/ et drone-management-service/.
"""
import re
import uuid

from flask import Flask, g, has_request_context, request

ENTETE = "X-Correlation-Id"
_VALIDE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def installer(app: Flask) -> None:
    @app.before_request
    def _lire():
        cid = request.headers.get(ENTETE, "")
        g.correlation_id = cid if _VALIDE.match(cid) else str(uuid.uuid4())

    @app.after_request
    def _renvoyer(reponse):
        if has_request_context() and "correlation_id" in g:
            reponse.headers[ENTETE] = g.correlation_id
        return reponse


def courant() -> str | None:
    return g.get("correlation_id") if has_request_context() else None
