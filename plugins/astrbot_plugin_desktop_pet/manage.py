"""Offline certificate, device pairing and revocation commands."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import uuid

from .protocol import Binding
from .store import Store


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def certificates(root: Path, host: str, *, renew=False) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    root.mkdir(parents=True, exist_ok=True)
    if not renew and any((root/name).exists() for name in ("ca.key", "ca.pem", "server.key", "server.pem")):
        raise ValueError("certificates_exist_use_a_new_directory_for_rotation")
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AstrBot Desktop Terminal CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
          .not_valid_after(now+timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(ca_key, hashes.SHA256()))
    if renew:
        ca_key = serialization.load_pem_private_key((root/"ca.key").read_bytes(), password=None)
        ca = x509.load_pem_x509_certificate((root/"ca.pem").read_bytes())
        if ca.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != ca_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("ca_key_mismatch")
        if ca.not_valid_after_utc < now + timedelta(days=366):
            raise ValueError("ca_near_expiry_repair_trust_before_renewal")
    key = ec.generate_private_key(ec.SECP256R1())
    address = x509.IPAddress(ipaddress.ip_address(host))
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
              .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
              .add_extension(x509.SubjectAlternativeName([address]), False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
              .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
              .sign(ca_key, hashes.SHA256()))
    if renew:
        stamp = now.strftime("%Y%m%d%H%M%S%f")
        for filename, content in (("server.key", key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
                ("server.pem", server.public_bytes(serialization.Encoding.PEM))):
            path = root/filename
            write_private(root/(filename+".backup-"+stamp), path.read_bytes())
            temp = root/(filename+".new-"+stamp)
            write_private(temp, content)
            os.replace(temp, path)
        return
    for filename, material in (("ca.key", ca_key), ("server.key", key)):
        write_private(root/filename, material.private_bytes(serialization.Encoding.PEM,
                       serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for filename, material in (("ca.pem", ca), ("server.pem", server)):
        write_private(root/filename, material.public_bytes(serialization.Encoding.PEM))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    cert = commands.add_parser("certificates")
    cert.add_argument("--ip", required=True)
    cert.add_argument("--renew", action="store_true", help="Keep the current private CA and renew the IP server certificate")
    pair = commands.add_parser("pair")
    for field in ("persona-id", "bot-id", "user-id", "qq-platform-id", "url", "output"):
        pair.add_argument("--" + field, required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("device_id")
    args = parser.parse_args()
    if args.command == "certificates":
        certificates(args.root, args.ip, renew=args.renew)
        return
    store = Store(args.root / "terminal.sqlite3")
    try:
        if args.command == "revoke":
            store.revoke(args.device_id)
            return
        from urllib.parse import urlparse
        url = urlparse(args.url)
        if url.scheme != "wss" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("expected_wss_url_without_credentials_or_query")
        output = Path(args.output)
        if not output.name.endswith(".local.json") or output.exists():
            raise ValueError("output_must_be_a_new_local_json_file")
        binding = Binding(uuid.uuid4().hex, args.persona_id, args.bot_id, args.user_id, args.qq_platform_id)
        ca = (args.root/"ca.pem").read_text()
        token = store.pair(binding)
        write_private(output, json.dumps({"v": 1, "url": args.url, "ca": ca, "device_id": binding.device_id,
                                         "token": token}, ensure_ascii=False, indent=2).encode())
        print("Pairing file created. Transfer through a trusted channel; import it in the desktop app.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
