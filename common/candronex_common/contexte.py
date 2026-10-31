"""Contexte de la requête en cours, propagé aux journaux et aux appels sortants."""
from contextvars import ContextVar

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
# Authorization reçu du client : recopié par D1 et D3, jamais journalisé.
authorization: ContextVar[str | None] = ContextVar("authorization", default=None)
