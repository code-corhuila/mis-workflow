from pathlib import Path
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
import jwt

from workflow.core import StepError
from workflow.model import identifier


class ServiceTokenFile:
    def __init__(self, token_path, public_key_path, subject, key_id):
        self.path = Path(token_path)
        self.subject = identifier(subject, "serviceSubject")
        if not isinstance(key_id, str) or not key_id:
            raise ValueError("Explicit service key id required")
        self.key_id = key_id
        with Path(public_key_path).open("rb") as source:
            self.public_key = serialization.load_pem_public_key(source.read(8193))
        if not isinstance(self.public_key, RSAPublicKey) or self.public_key.key_size < 2048:
            raise ValueError("RSA public key of at least 2048 bits required")

    def __call__(self):
        try:
            # Read each step: replace the mounted file atomically to renew, keeping the same subject.
            with self.path.open("rb") as source:
                raw = source.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            token = raw.decode("ascii").strip()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or header.get("kid") != self.key_id:
                raise ValueError()
            claims = jwt.decode(token, self.public_key, algorithms=["RS256"],
                audience="mis-api", issuer="mis-security", leeway=0,
                options={"require": ["sub", "exp", "iat", "iss", "aud", "roles", "service"]})
            if (claims["sub"] != self.subject or claims["service"] != "mis-workflow"
                    or claims["roles"] != ["INVENTORY"]
                    or type(claims["exp"]) is not int or type(claims["iat"]) is not int
                    or not 0 < claims["exp"] - claims["iat"] <= 3600
                    or claims["exp"] <= time.time() + 5):
                raise ValueError()
            return token
        except (OSError, UnicodeError, ValueError, TypeError, jwt.InvalidTokenError):
            raise StepError("SERVICE_IDENTITY_UNAVAILABLE", True) from None
