"""AES-128 / ECB / ZeroPadding —— 纯标准库实现。

校园网门户（raas 门户）在浏览器端用 CryptoJS 把口令加密后再提交：
    pass = hex( AES-128-ECB(key="5a3b9f207411a8ed", zero_pad(随机4位前缀 + 明文口令)) )
这里用纯 Python 复刻该算法，避免任何第三方依赖。
"""

from __future__ import annotations

import os
import random

# ---------------------------------------------------------------- AES 常量

SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)

BLOCK = 16


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法。"""
    out = 0
    for _ in range(8):
        if b & 1:
            out ^= a
        b >>= 1
        a = _xtime(a)
    return out & 0xFF


def expand_key(key: bytes) -> list[bytes]:
    """AES-128 密钥扩展，返回 11 组轮密钥（每组 16 字节）。"""
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥")
    words = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]                     # RotWord
            temp = [SBOX[b] for b in temp]                 # SubWord
            temp[0] ^= RCON[i // 4 - 1]
        words.append([words[i - 4][j] ^ temp[j] for j in range(4)])
    return [bytes(sum(words[r * 4:r * 4 + 4], [])) for r in range(11)]


def _add_round_key(state: list[int], key: bytes) -> None:
    for i in range(16):
        state[i] ^= key[i]


def _sub_bytes(state: list[int]) -> None:
    for i in range(16):
        state[i] = SBOX[state[i]]


def _shift_rows(s: list[int]) -> None:
    # 扁平布局：s[r + 4c]，行 r 循环左移 r 位
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            s[r + 4 * c] = row[c]


def _mix_columns(s: list[int]) -> None:
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
        s[i] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
        s[i + 1] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
        s[i + 2] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
        s[i + 3] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)


def encrypt_block(round_keys: list[bytes], block: bytes) -> bytes:
    if len(block) != BLOCK:
        raise ValueError("AES 分组必须是 16 字节")
    state = list(block)
    _add_round_key(state, round_keys[0])
    for rnd in range(1, 10):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[rnd])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, round_keys[10])
    return bytes(state)


def encrypt_ecb(key: bytes, data: bytes) -> bytes:
    if len(data) % BLOCK:
        raise ValueError("ECB 数据长度必须是 16 的整数倍")
    round_keys = expand_key(key)
    return b"".join(encrypt_block(round_keys, data[i:i + BLOCK])
                    for i in range(0, len(data), BLOCK))


def zero_pad(data: bytes, block: int = BLOCK) -> bytes:
    """CryptoJS ZeroPadding：仅在未对齐时补 \\x00。"""
    rest = len(data) % block
    if rest:
        data += b"\x00" * (block - rest)
    return data


# ------------------------------------------------------------- 门户口令编码

# 与门户 index.js 中 get_random_str() 使用的字符表完全一致（共 61 个字符）
PREFIX_MAP = "abacdefghjklmnopqrstuvwxyzABCDEFGHJKLMNOPQRSTUVWXYZ0123456789"
PORTAL_KEY = b"5a3b9f207411a8ed"     # ASCII，16 字节
MAX_PASSWORD_LEN = 24


class PasswordTooLong(ValueError):
    pass


def encode_password(password: str, prefix: str | None = None) -> str:
    """把明文口令编码成门户要求的 32 位十六进制串。

    prefix 仅用于自测/复现，正常运行随机生成 4 位。
    """
    if len(password) == 32 and all(c in "0123456789abcdefABCDEF" for c in password):
        # 门户对已经是 32 位十六进制串的输入原样透传
        return password
    if len(password) >= MAX_PASSWORD_LEN:
        raise PasswordTooLong(f"口令长度不能超过 {MAX_PASSWORD_LEN - 1} 个字符")
    if prefix is None:
        prefix = "".join(random.choice(PREFIX_MAP) for _ in range(4))
    plain = zero_pad((prefix + password).encode("utf-8"))
    return encrypt_ecb(PORTAL_KEY, plain).hex()


if __name__ == "__main__":
    import sys

    pw = sys.argv[1] if len(sys.argv) > 1 else "test1234"
    print(encode_password(pw, prefix=sys.argv[2] if len(sys.argv) > 2 else None))
    for v in ("/dev/urandom",):
        if os.path.exists(v):
            pass
