from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import time
from pathlib import Path
from typing import Any


KEY_BITS = 2048
PUBLIC_EXPONENT = 65537
HASH_NAME = "SHA-256"
MAX_PAYLOAD_AGE_SECONDS = 5 * 60
_LOGIN_RSA_KEY: dict[str, int] | None = None
_LEGACY_KEY_FILE_CLEANED = False


class LoginPayloadError(ValueError):
    pass


def get_login_public_jwk() -> dict[str, Any]:
    key = _load_or_create_key()
    return {
        "kty": "RSA",
        "n": _int_to_b64url(key["n"]),
        "e": _int_to_b64url(key["e"]),
        "alg": "RSA-OAEP-256",
        "ext": True,
        "key_ops": ["encrypt"],
    }


def decrypt_login_payload(cipher_text: str) -> dict[str, str]:
    if not cipher_text:
        raise LoginPayloadError("missing encrypted payload")
    key = _load_or_create_key()
    try:
        algorithm, encrypted_text = _split_cipher_payload(cipher_text)
        encrypted = _b64url_to_bytes(encrypted_text)
        plain = _decrypt_by_algorithm(encrypted, key["n"], key["d"], algorithm)
        data = json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise LoginPayloadError("invalid encrypted payload") from exc

    timestamp = int(data.get("ts") or 0)
    if timestamp and abs(int(time.time() * 1000) - timestamp) > MAX_PAYLOAD_AGE_SECONDS * 1000:
        raise LoginPayloadError("expired encrypted payload")

    return {
        "username": str(data.get("username") or ""),
        "password": str(data.get("password") or ""),
        "mfa_code": str(data.get("mfa_code") or ""),
    }


def _split_cipher_payload(cipher_text: str) -> tuple[str, str]:
    if ":" not in cipher_text:
        return "oaep256", cipher_text
    algorithm, encrypted_text = cipher_text.split(":", 1)
    if algorithm not in {"oaep256", "pkcs1v15"} or not encrypted_text:
        raise LoginPayloadError("unsupported encrypted payload")
    return algorithm, encrypted_text


def _decrypt_by_algorithm(cipher_text: bytes, n: int, d: int, algorithm: str) -> bytes:
    if algorithm == "oaep256":
        return _rsa_oaep_decrypt(cipher_text, n, d)
    if algorithm == "pkcs1v15":
        return _rsa_pkcs1_v15_decrypt(cipher_text, n, d)
    raise LoginPayloadError("unsupported encrypted payload")


def _load_or_create_key() -> dict[str, int]:
    global _LOGIN_RSA_KEY
    _cleanup_legacy_key_file()
    if _LOGIN_RSA_KEY is None:
        _LOGIN_RSA_KEY = _generate_rsa_key(KEY_BITS)
    return _LOGIN_RSA_KEY


def _cleanup_legacy_key_file() -> None:
    global _LEGACY_KEY_FILE_CLEANED
    if _LEGACY_KEY_FILE_CLEANED:
        return
    _LEGACY_KEY_FILE_CLEANED = True
    legacy_path = Path(os.getenv("APP_DATA_DIR", "data")).resolve() / "login-rsa-key.json"
    try:
        legacy_path.unlink(missing_ok=True)
    except OSError:
        pass


def _generate_rsa_key(bits: int) -> dict[str, int]:
    half_bits = bits // 2
    while True:
        p = _generate_prime(half_bits)
        q = _generate_prime(bits - half_bits)
        if p == q:
            continue
        n = p * q
        phi = (p - 1) * (q - 1)
        if n.bit_length() == bits and math.gcd(PUBLIC_EXPONENT, phi) == 1:
            d = pow(PUBLIC_EXPONENT, -1, phi)
            return {"n": n, "e": PUBLIC_EXPONENT, "d": d}


def _generate_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits)
        candidate |= (1 << (bits - 1)) | 1
        if math.gcd(candidate - 1, PUBLIC_EXPONENT) != 1:
            continue
        if _is_probable_prime(candidate):
            return candidate


def _is_probable_prime(value: int, rounds: int = 32) -> bool:
    if value < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for prime in small_primes:
        if value == prime:
            return True
        if value % prime == 0:
            return False

    d = value - 1
    s = 0
    while d % 2 == 0:
        s += 1
        d //= 2

    for _ in range(rounds):
        a = secrets.randbelow(value - 3) + 2
        x = pow(a, d, value)
        if x in (1, value - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, value)
            if x == value - 1:
                break
        else:
            return False
    return True


def _rsa_oaep_decrypt(cipher_text: bytes, n: int, d: int) -> bytes:
    key_size = (n.bit_length() + 7) // 8
    if len(cipher_text) != key_size:
        raise LoginPayloadError("invalid ciphertext length")
    encrypted_number = int.from_bytes(cipher_text, "big")
    if encrypted_number >= n:
        raise LoginPayloadError("ciphertext out of range")

    encoded_message = pow(encrypted_number, d, n).to_bytes(key_size, "big")
    return _oaep_decode(encoded_message)


def _rsa_pkcs1_v15_decrypt(cipher_text: bytes, n: int, d: int) -> bytes:
    key_size = (n.bit_length() + 7) // 8
    if len(cipher_text) != key_size:
        raise LoginPayloadError("invalid ciphertext length")
    encrypted_number = int.from_bytes(cipher_text, "big")
    if encrypted_number >= n:
        raise LoginPayloadError("ciphertext out of range")

    encoded_message = pow(encrypted_number, d, n).to_bytes(key_size, "big")
    if len(encoded_message) < 11 or encoded_message[0] != 0 or encoded_message[1] != 2:
        raise LoginPayloadError("invalid PKCS1 block")
    separator = encoded_message.find(b"\x00", 2)
    if separator < 10:
        raise LoginPayloadError("invalid PKCS1 padding")
    return encoded_message[separator + 1:]


def _oaep_decode(encoded_message: bytes) -> bytes:
    hash_len = hashlib.sha256().digest_size
    if len(encoded_message) < 2 * hash_len + 2 or encoded_message[0] != 0:
        raise LoginPayloadError("invalid OAEP block")

    masked_seed = encoded_message[1:1 + hash_len]
    masked_db = encoded_message[1 + hash_len:]
    seed = _xor_bytes(masked_seed, _mgf1(masked_db, hash_len))
    db = _xor_bytes(masked_db, _mgf1(seed, len(masked_db)))
    label_hash = hashlib.sha256(b"").digest()
    if not secrets.compare_digest(db[:hash_len], label_hash):
        raise LoginPayloadError("invalid OAEP label")

    index = hash_len
    while index < len(db) and db[index] == 0:
        index += 1
    if index >= len(db) or db[index] != 1:
        raise LoginPayloadError("invalid OAEP padding")
    return db[index + 1:]


def _mgf1(seed: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _int_to_b64url(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    return _bytes_to_b64url(raw)


def _b64url_to_int(value: str) -> int:
    return int.from_bytes(_b64url_to_bytes(value), "big")


def _bytes_to_b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_to_bytes(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
