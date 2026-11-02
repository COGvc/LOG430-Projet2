"""Génère krakend.json (routes publiques).

    python krakend/generer.py > krakend/krakend.json

Chaque route : validation du JWT, azp copié dans X-Client-Id, en-têtes transmis, réponse du service
telle quelle (no-op). Les routes /internal/v1/... ne figurent jamais ici.
"""
import json

DRONE = "http://drone-management-service:8000"
ORDERING = "http://service-ordering-service:8000"
SLICE = "http://slice-management-service:8000"

ROUTES = [  # (méthode, chemin, service, paramètres de requête permis)
    ("POST", "/api/drone-management/v1/drone", DRONE, []),
    ("GET", "/api/drone-management/v1/drone", DRONE, ["limit"]),
    ("GET", "/api/drone-management/v1/drone/{droneId}", DRONE, []),
    ("GET", "/api/service-ordering/v1/catalog", ORDERING, []),
    ("POST", "/api/service-ordering/v1/serviceOrder", ORDERING, []),
    ("GET", "/api/service-ordering/v1/serviceOrder", ORDERING, ["limit"]),
    ("GET", "/api/service-ordering/v1/serviceOrder/{orderId}", ORDERING, []),
    ("GET", "/api/slice-management/v1/service", SLICE, ["droneId", "orderId"]),
]

VALIDATEUR = {
    "alg": "RS256",
    "jwk_url": "http://keycloak:8080/realms/candronex/protocol/openid-connect/certs",
    "disable_jwk_security": True,
    "issuer": "http://localhost:8081/realms/candronex",
    "audience": ["candronex-api"],
    "cache": True,
    "propagate_claims": [["azp", "X-Client-Id"]],
}


def route(methode, chemin, hote, params):
    r = {
        "endpoint": chemin,
        "method": methode,
        "input_headers": ["Authorization", "X-Client-Id", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
        "output_encoding": "no-op",
        "backend": [{"host": [hote], "url_pattern": chemin, "encoding": "no-op"}],
        "extra_config": {"auth/validator": VALIDATEUR},
    }
    if params:
        r["input_query_strings"] = params
    return r


config = {
    "$schema": "https://www.krakend.io/schema/v2.7/krakend.json",
    "version": 3,
    "name": "CanDroneX API Gateway",
    "port": 8080,
    "timeout": "5s",
    "output_encoding": "no-op",
    "extra_config": {
        "security/cors": {
            "allow_origins": ["*"],
            "allow_methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
            "expose_headers": ["X-Correlation-Id"],
        }
    },
    "endpoints": [route(*r) for r in ROUTES],
}

print(json.dumps(config, indent=2, ensure_ascii=False))
