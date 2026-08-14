"""Platform-provided file download (uploads from the host tool call)."""
from __future__ import annotations


def download_file_bytes(url: str, *, timeout: int = 60, max_bytes: int = 20_000_000) -> bytes:
    """Download a host-provided upload URL.

    These URLs come from the platform's ``files`` parameter (trusted, often
    internal hostnames), so unlike model-initiated fetching they skip SSRF
    validation — same trust level as mini_claw. Scheme is still checked.
    """
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid file url: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "my_claw/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes)
