"""Pack web/index.html into the gzipped page the ESP32 sketch serves.

Run this from the repository root after editing the page:

    python tools/pack_page.py
"""

import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "web" / "index.html"
HEADER_PATH = ROOT / "web_page_gz.h"


def main() -> None:
    html = HTML_PATH.read_bytes()
    if b"async function loadHistory" not in html or b"async function pollLatest" not in html:
        raise SystemExit("web/index.html is missing the incremental history loader")
    packed = gzip.compress(html, compresslevel=9, mtime=0)
    if gzip.decompress(packed) != html:
        raise SystemExit("gzip round-trip failed")

    lines = []
    for offset in range(0, len(packed), 16):
        chunk = packed[offset : offset + 16]
        lines.append("  " + ", ".join(f"0x{byte:02x}" for byte in chunk) + ",")

    header = f"""#pragma once

// Generated from web/index.html by tools/pack_page.py. Do not edit.
// {len(html)} bytes of HTML, gzipped to {len(packed)} bytes.

#include <stdint.h>
#include <stddef.h>

static const size_t kIndexHtmlGzLen = {len(packed)};
static const uint8_t kIndexHtmlGz[] PROGMEM = {{
{chr(10).join(lines)}
}};
"""
    HEADER_PATH.write_text(header, encoding="utf-8", newline="\n")
    print(f"Wrote {HEADER_PATH.name}: {len(html)} -> {len(packed)} bytes")


if __name__ == "__main__":
    main()
