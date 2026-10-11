import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError, PyJWKClientConnectionError

from workflow.model import Failure, identifier


class HumanTokens:
    def __init__(self, jwks_url):
        self.keys = PyJWKClient(jwks_url, timeout=2, lifespan=300)

    def authenticate(self, authorization):
        try:
            if not authorization or len(authorization.split()) != 2:
                raise InvalidTokenError()
            scheme, token = authorization.split()
            if scheme.lower() != "bearer" or len(token) > 16384:
                raise InvalidTokenError()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise InvalidTokenError()
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience="mis-api", issuer="mis-security",
                leeway=0, options={"require": ["sub", "exp", "iss", "aud", "roles"]})
            identifier(claims["sub"], "actor")
            roles = claims["roles"]
            if (type(claims["exp"]) is not int or not isinstance(roles, list) or not roles
                    or any(not isinstance(role, str) or role not in {"ADMIN", "INVENTORY", "SUPERVISOR", "CONSULTATION"} for role in roles)):
                raise InvalidTokenError()
        except PyJWKClientConnectionError:
            raise Failure(503, "SERVICE_UNAVAILABLE") from None
        except (InvalidTokenError, PyJWKClientError, Failure, ValueError, TypeError, AttributeError):
            raise Failure(401, "UNAUTHORIZED") from None
        if "service" in claims:
            raise Failure(403, "FORBIDDEN")
        return claims
