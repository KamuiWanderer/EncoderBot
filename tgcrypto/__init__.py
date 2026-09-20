"""
Fast TgCrypto accelerated cryptographic module using cryptg and PyCryptodome AES-NI.
Provides ige256_encrypt, ige256_decrypt, ctr256_encrypt, ctr256_decrypt, cbc256_encrypt, cbc256_decrypt.
"""

from __future__ import annotations

import cryptg
from Crypto.Cipher import AES

__version__ = "1.2.5"


def ige256_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256 IGE Encryption accelerated by cryptg C/Rust extension."""
    return cryptg.encrypt_ige(data, key, iv)


def ige256_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256 IGE Decryption accelerated by cryptg C/Rust extension."""
    return cryptg.decrypt_ige(data, key, iv)


def cbc256_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256 CBC Encryption accelerated by PyCryptodome C/AES-NI."""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(data)


def cbc256_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256 CBC Decryption accelerated by PyCryptodome C/AES-NI."""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.decrypt(data)


def ctr256_encrypt(data: bytes, key: bytes, iv: bytearray, state: bytearray = None) -> bytes:
    """AES-256 CTR Encryption with state preservation accelerated by PyCryptodome."""
    state = state or bytearray(1)
    cipher = AES.new(key, AES.MODE_ECB)
    out = bytearray(data)
    chunk = cipher.encrypt(bytes(iv))
    for i in range(0, len(data), 16):
        for j in range(0, min(len(data) - i, 16)):
            out[i + j] ^= chunk[state[0]]
            state[0] += 1
            if state[0] >= 16:
                state[0] = 0
            if state[0] == 0:
                for k in range(15, -1, -1):
                    try:
                        iv[k] += 1
                        break
                    except ValueError:
                        iv[k] = 0
                chunk = cipher.encrypt(bytes(iv))
    return bytes(out)


def ctr256_decrypt(data: bytes, key: bytes, iv: bytearray, state: bytearray = None) -> bytes:
    """AES-256 CTR Decryption with state preservation accelerated by PyCryptodome."""
    return ctr256_encrypt(data, key, iv, state)
