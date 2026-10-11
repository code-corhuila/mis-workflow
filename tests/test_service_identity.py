from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from workflow.core import StepError
from workflow.service_identity import ServiceTokenFile


class ServiceIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_path = Path(self.directory.name) / "service.jwt"
        self.key_path = Path(self.directory.name) / "public.pem"
        self.key_path.write_bytes(self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.subject, self.key_id = str(uuid4()), "workflow-test-key"
        self.provider = ServiceTokenFile(self.token_path, self.key_path, self.subject, self.key_id)

    def mint(self, changes=None, key=None, kid=None):
        now = int(time.time())
        claims = {"sub": self.subject, "iss": "mis-security", "aud": "mis-api", "iat": now,
                  "exp": now + 300, "roles": ["INVENTORY"], "service": "mis-workflow"}
        claims.update(changes or {})
        token = jwt.encode(claims, key or self.private, algorithm="RS256", headers={"kid": kid or self.key_id})
        self.token_path.write_text(token, encoding="ascii")
        return token

    def rejected(self):
        with self.assertRaises(StepError) as error:
            self.provider()
        self.assertEqual(("SERVICE_IDENTITY_UNAVAILABLE", True), (error.exception.code, error.exception.retryable))

    def test_renewal_reads_new_file_preserving_service_subject(self):
        original = self.mint()
        self.assertEqual(original, self.provider())
        renewed = self.mint({"jti": str(uuid4())})
        self.assertNotEqual(original, renewed)
        self.assertEqual(renewed, self.provider())

    def test_invalid_identity_and_dates_are_rejected(self):
        now = int(time.time())
        for changes in ({"sub": str(uuid4())}, {"service": "other"}, {"service": None},
                        {"roles": ["ADMIN", "INVENTORY"]}, {"roles": "INVENTORY"},
                        {"iss": "other"}, {"aud": "other"}, {"exp": now - 1},
                        {"exp": now + 3}, {"iat": now + 60}, {"exp": str(now + 300)},
                        {"exp": now + 7200}, {"exp": True}):
            with self.subTest(changes=changes):
                self.mint(changes)
                self.rejected()

    def test_wrong_signature_kid_and_algorithm_are_rejected(self):
        self.mint(key=self.wrong)
        self.rejected()
        self.mint(kid="other-key")
        self.rejected()
        self.token_path.write_text(jwt.encode({"sub": self.subject}, "synthetic-unit-test-only-key-32chars",
                                             algorithm="HS256", headers={"kid": self.key_id}), encoding="ascii")
        self.rejected()

    def test_missing_oversized_malformed_or_private_key_files_fail_closed(self):
        self.rejected()
        for value in (b"not-a-jwt", b"x" * 16385, b"\xff"):
            self.token_path.write_bytes(value)
            self.rejected()
        self.key_path.write_bytes(self.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        with self.assertRaises(ValueError):
            ServiceTokenFile(self.token_path, self.key_path, self.subject, self.key_id)
