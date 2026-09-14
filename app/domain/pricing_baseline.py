import base64
import hashlib
import json
import os
from pathlib import Path
from secrets import token_bytes

PRICING_VERSION = "catalog-v2-stage2"

OBFUSCATION_FORMAT = "pricing-baseline-obf-v1"
KEY_ENV = "PRICING_BASELINE_KEY"
STRICT_ENV = "PRICING_BASELINE_STRICT"


def _as_bool(value, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _keystream(secret_key_bytes, nonce_bytes, size):
    buf = b""
    counter = 0
    while len(buf) < size:
        block = hashlib.sha256(
            secret_key_bytes + nonce_bytes + counter.to_bytes(4, "big")
        ).digest()
        buf += block
        counter += 1
    return buf[:size]


def _xor_bytes(left, right):
    return bytes(a ^ b for a, b in zip(left, right))


def encode_payload(plain_json_text: str, secret_key: str, nonce_hex: str | None = None) -> dict:
    plain_bytes = plain_json_text.encode("utf-8")
    nonce = bytes.fromhex(nonce_hex) if nonce_hex else token_bytes(8)
    cipher_bytes = _xor_bytes(
        plain_bytes,
        _keystream(secret_key.encode("utf-8"), nonce, len(plain_bytes)),
    )
    return {
        "format": OBFUSCATION_FORMAT,
        "encoding": "base64",
        "nonce": nonce.hex(),
        "payload": base64.b64encode(cipher_bytes).decode("ascii"),
        "sha256": hashlib.sha256(plain_bytes).hexdigest(),
    }


def decode_payload(payload_obj: dict, secret_key: str) -> str:
    if payload_obj.get("format") != OBFUSCATION_FORMAT:
        raise ValueError("不支持的混淆文件格式")
    if payload_obj.get("encoding") != "base64":
        raise ValueError("不支持的混淆编码")
    nonce = bytes.fromhex(str(payload_obj.get("nonce", "")))
    cipher_bytes = base64.b64decode(payload_obj.get("payload", ""))
    plain_bytes = _xor_bytes(
        cipher_bytes,
        _keystream(secret_key.encode("utf-8"), nonce, len(cipher_bytes)),
    )
    if hashlib.sha256(plain_bytes).hexdigest() != payload_obj.get("sha256"):
        raise ValueError("混淆文件校验失败（sha256 不匹配）")
    return plain_bytes.decode("utf-8")


def _decode_obf(obf_path: Path, secret_key: str) -> dict:
    payload = json.loads(obf_path.read_text(encoding="utf-8"))
    return json.loads(decode_payload(payload, secret_key))


def load_baseline(json_path: Path, obf_path: Path | None = None) -> dict:
    """Load pricing baseline, preferring obfuscated file at runtime.

    Resolution order (non-strict):
      1. obf_path + PRICING_BASELINE_KEY   → decode in memory (preferred)
      2. json_path                         → plaintext fallback (migration output / tests)
      3. otherwise                         → raise (no silent empty fallback — see bug #1)

    Strict mode (PRICING_BASELINE_STRICT=1) forces path 1 and refuses plaintext.
    """
    json_path = Path(json_path)
    obf_path = Path(obf_path) if obf_path else json_path.with_suffix(".obf")

    strict = _as_bool(os.getenv(STRICT_ENV), default=False)
    secret_key = os.getenv(KEY_ENV)

    if strict:
        if not obf_path.exists():
            raise RuntimeError(
                f"Strict 模式要求混淆基线 {obf_path}，但文件不存在"
            )
        if not secret_key:
            raise RuntimeError(
                f"Strict 模式要求环境变量 {KEY_ENV}，但未设置"
            )
        return _decode_obf(obf_path, secret_key)

    if obf_path.exists() and secret_key:
        return _decode_obf(obf_path, secret_key)

    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    if obf_path.exists():
        raise RuntimeError(
            f"检测到混淆基线 {obf_path}，但未配置 {KEY_ENV}；"
            f"请设置密钥，或运行 ops/migrate_baseline.py 生成明文 {json_path}"
        )

    raise FileNotFoundError(
        f"未找到价格基线：{obf_path} 或 {json_path}。"
        f"请提交混淆基线并配置 {KEY_ENV}，或运行 ops/migrate_baseline.py 生成明文基线。"
    )


def pricing_version() -> str:
    return PRICING_VERSION
