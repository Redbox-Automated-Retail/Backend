"""Generated placeholder media: solid-colour PNG images and a one-page XPS document.

Used for seeded ads, start-screen images and planograms whose content has not been uploaded.
"""
import functools
import io
import struct
import zipfile
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _rgb(color: str):
    c = (color or "#c8102e").lstrip("#")
    if len(c) != 6:
        c = "c8102e"
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


@functools.lru_cache(maxsize=64)
def png(width: int, height: int, color: str = "#c8102e") -> bytes:
    """A valid RGB PNG: solid colour with a white 4 px border and a white diagonal band."""
    width = max(8, min(int(width), 4096))
    height = max(8, min(int(height), 4096))
    r, g, b = _rgb(color)
    fill = bytes((r, g, b))
    white = b"\xff\xff\xff"
    rows = []
    band = max(4, min(width, height) // 10)
    for y in range(height):
        if y < 4 or y >= height - 4:
            row = white * width
        else:
            start = int(y * width / height)
            row = bytearray(fill * width)
            row[0:12] = white * 4
            row[(width - 4) * 3:] = white * 4
            lo, hi = max(4, start - band // 2), min(width - 4, start + band // 2)
            if hi > lo:
                row[lo * 3:hi * 3] = white * (hi - lo)
            row = bytes(row)
        rows.append(b"\x00" + row)
    raw = zlib.compress(b"".join(rows), 9)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", raw) + _chunk(b"IEND", b"")


@functools.lru_cache(maxsize=16)
def xps(title: str = "Planogram") -> bytes:
    """Minimal valid XPS (OPC zip): one Letter page with a frame and a grid of slot boxes."""
    w, h = 816, 1056
    paths = [f'<Path Stroke="#000000" StrokeThickness="2" Data="M 48,48 L {w - 48},48 {w - 48},{h - 48} 48,{h - 48} Z" />']
    cols, rows = 6, 15
    cw, rh = (w - 144) / cols, (h - 216) / rows
    for r in range(rows):
        for c in range(cols):
            x, y = 72 + c * cw, 168 + r * rh
            paths.append(f'<Path Stroke="#808080" StrokeThickness="1" '
                         f'Data="M {x:.1f},{y:.1f} L {x + cw - 6:.1f},{y:.1f} {x + cw - 6:.1f},{y + rh - 6:.1f} {x:.1f},{y + rh - 6:.1f} Z" />')
    paths.append('<Path Fill="#C8102E" Data="M 72,72 L 744,72 744,132 72,132 Z" />')
    page = ('<FixedPage xmlns="http://schemas.microsoft.com/xps/2005/06" Width="%d" Height="%d" xml:lang="en-US">%s'
            '</FixedPage>') % (w, h, "".join(paths))
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
            '<Default Extension="fdseq" ContentType="application/vnd.ms-package.xps-fixeddocumentsequence+xml" />'
            '<Default Extension="fdoc" ContentType="application/vnd.ms-package.xps-fixeddocument+xml" />'
            '<Default Extension="fpage" ContentType="application/vnd.ms-package.xps-fixedpage+xml" />'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Type="http://schemas.microsoft.com/xps/2005/06/fixedrepresentation" '
            'Target="/FixedDocSeq.fdseq" Id="R0" /></Relationships>'),
        "FixedDocSeq.fdseq": (
            '<FixedDocumentSequence xmlns="http://schemas.microsoft.com/xps/2005/06">'
            '<DocumentReference Source="/Documents/1/FixedDoc.fdoc" /></FixedDocumentSequence>'),
        "Documents/1/FixedDoc.fdoc": (
            '<FixedDocument xmlns="http://schemas.microsoft.com/xps/2005/06">'
            '<PageContent Source="/Documents/1/Pages/1.fpage" /></FixedDocument>'),
        "Documents/1/Pages/1.fpage": page,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()
