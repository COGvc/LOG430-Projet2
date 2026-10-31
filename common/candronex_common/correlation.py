"""X-Correlation-Id : repris s'il est valide, sinon généré; renvoyé dans la réponse.

Middleware ASGI pur (sans BaseHTTPMiddleware) : la valeur posée dans le contexte reste visible
du point d'entrée, des dépendances et des journaux de la requête.
"""
import re
import uuid

from .contexte import correlation_id

ENTETE = "X-Correlation-Id"
_VALIDE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class CorrelationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        recu = dict(scope["headers"]).get(b"x-correlation-id", b"").decode("latin-1")
        cid = recu if _VALIDE.match(recu) else str(uuid.uuid4())
        correlation_id.set(cid)

        async def envoyer(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"x-correlation-id", cid.encode())]
            await send(message)

        await self.app(scope, receive, envoyer)
