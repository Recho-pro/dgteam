from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import Final

from Crypto.Cipher import AES


BLOCK_SIZE: Final[int] = 32


class WecomCryptoError(Exception):
    pass


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = [str(token or ""), str(timestamp or ""), str(nonce or ""), str(encrypted or "")]
    parts.sort()
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    if pad_len == 0:
        pad_len = BLOCK_SIZE
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise WecomCryptoError("Cannot unpad an empty payload.")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > BLOCK_SIZE:
        raise WecomCryptoError("Invalid PKCS7 padding.")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise WecomCryptoError("Corrupted PKCS7 padding.")
    return data[:-pad_len]


class WecomCallbackCrypto:
    def __init__(self, *, token: str, encoding_aes_key: str, receive_id: str):
        self.token = str(token or "").strip()
        self.receive_id = str(receive_id or "").strip()
        self.aes_key = self._decode_aes_key(encoding_aes_key)
        self.iv = self.aes_key[:16]

    @staticmethod
    def _decode_aes_key(encoding_aes_key: str) -> bytes:
        raw = str(encoding_aes_key or "").strip()
        if not raw:
            raise WecomCryptoError("Missing EncodingAESKey.")
        padding = "=" * ((4 - len(raw) % 4) % 4)
        try:
            decoded = base64.b64decode(raw + padding)
        except Exception as exc:  # pragma: no cover - defensive
            raise WecomCryptoError("EncodingAESKey is not valid base64.") from exc
        if len(decoded) != 32:
            raise WecomCryptoError("EncodingAESKey must decode to 32 bytes.")
        return decoded

    def verify_signature(self, *, msg_signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
        expected = _sha1_signature(self.token, timestamp, nonce, encrypted)
        return expected == str(msg_signature or "").strip()

    def decrypt_echo(self, *, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        return self.decrypt_message(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echostr,
        )

    def decrypt_message(self, *, msg_signature: str, timestamp: str, nonce: str, encrypted: str) -> str:
        if not self.verify_signature(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        ):
            raise WecomCryptoError("Invalid callback signature.")

        try:
            encrypted_bytes = base64.b64decode(str(encrypted or ""))
        except Exception as exc:
            raise WecomCryptoError("Encrypted callback payload is not valid base64.") from exc

        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        decrypted = _pkcs7_unpad(cipher.decrypt(encrypted_bytes))
        if len(decrypted) < 20:
            raise WecomCryptoError("Decrypted callback payload is too short.")

        xml_length = struct.unpack(">I", decrypted[16:20])[0]
        xml_start = 20
        xml_end = xml_start + xml_length
        xml_bytes = decrypted[xml_start:xml_end]
        receive_id = decrypted[xml_end:].decode("utf-8")
        if self.receive_id and receive_id != self.receive_id:
            raise WecomCryptoError("Callback receive_id does not match configured CorpID.")
        return xml_bytes.decode("utf-8")

    def encrypt_message(self, plaintext: str, *, timestamp: str, nonce: str) -> dict[str, str]:
        plain_bytes = str(plaintext or "").encode("utf-8")
        payload = (
            os.urandom(16)
            + struct.pack(">I", len(plain_bytes))
            + plain_bytes
            + self.receive_id.encode("utf-8")
        )
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        encrypted = cipher.encrypt(_pkcs7_pad(payload))
        encrypted_b64 = base64.b64encode(encrypted).decode("utf-8")
        msg_signature = _sha1_signature(self.token, timestamp, nonce, encrypted_b64)
        return {
            "Encrypt": encrypted_b64,
            "MsgSignature": msg_signature,
            "TimeStamp": str(timestamp or ""),
            "Nonce": str(nonce or ""),
        }
