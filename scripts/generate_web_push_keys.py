"""Run once per environment; store the generated keys in Railway variables.

Never commit the generated private key. Running this script does not send push.
"""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


if __name__ == "__main__":
    private = ec.generate_private_key(ec.SECP256R1())
    public_bytes = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private_bytes = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    print("WEB_PUSH_PUBLIC_KEY=" + encode(public_bytes))
    print("WEB_PUSH_PRIVATE_KEY=" + encode(private_bytes))
    print("Set WEB_PUSH_SUBJECT to mailto:<administrator-email> in Railway.")
