"""Journalisation structurée (une ligne JSON par événement), avec l'identifiant de corrélation."""
import json
import logging
import sys
from datetime import datetime, timezone

from .contexte import correlation_id

_SERVICE = "candronex"


class _FormatJson(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        ligne = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "niveau": r.levelname,
            "service": _SERVICE,
            "evenement": r.getMessage(),
            "correlationId": correlation_id.get(),
        }
        ligne.update(getattr(r, "champs", {}))
        if r.exc_info:
            ligne["exception"] = self.formatException(r.exc_info)
        return json.dumps(ligne, ensure_ascii=False, default=str)


def configurer(service: str) -> None:
    global _SERVICE
    _SERVICE = service
    sortie = logging.StreamHandler(sys.stdout)
    sortie.setFormatter(_FormatJson())
    racine = logging.getLogger()
    racine.handlers[:] = [sortie]
    racine.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def evenement(message: str, niveau: int = logging.INFO, **champs) -> None:
    """Ex. evenement("drone enregistré", droneId="DRN-0231", imsi=str(imsi_masque))."""
    logging.getLogger(_SERVICE).log(niveau, message, extra={"champs": champs})
