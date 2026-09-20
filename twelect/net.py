"""共用的 HTTP 下載設定。

python.org 版的 macOS Python 預設不吃系統鑰匙圈，沒跑過
「Install Certificates.command」就會在 https 上拿到
CERTIFICATE_VERIFY_FAILED，所以這裡優先用 certifi 的憑證庫。
"""

from __future__ import annotations

import ssl
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) twelect/0.1 (+local research tool)"


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - 沒有 certifi 就退回系統預設
        return ssl.create_default_context()


_CTX = ssl_context()


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        return resp.read()
