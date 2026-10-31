"""inventory.data: Redbox's barcode -> title archive (software/src/Common/Redbox.ProductLookupCatalog/Archive.cs).

    header (312 bytes): "<~look~>", version 0, flags 0, origin machine (32 ASCII, right-aligned with spaces),
                        created on (14 ASCII, yyyyMMddHHmmss), 256 zero bytes
    records (18 bytes): prefix (byte, leading zeros of the barcode), barcode digits (uint64), title id (uint32),
                        status (byte, InventoryStatusCode), total rental count (uint32), little-endian

The engine looks barcodes up by binary search with `string.CompareTo`, so records must be sorted by barcode;
for digit-only strings that is ordinal order. A real file holds ~26 million records (464 MB).
"""
import datetime as dt
import struct

MAGIC = b"<~look~>"
HEADER_SIZE = 312
RECORD = struct.Struct("<BQIBI")
STATUS = {0: "Known", 1: "WrongTitle", 2: "Damaged", 3: "Thinned", 4: "PR1", 5: "PR2", 6: "Fraud",
          7: "Rebalancing", 8: "Redeployment"}


class InventoryError(ValueError):
    pass


def header(origin_machine="UPDATESVC01", created_on=None):
    created = (created_on or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%d%H%M%S")
    machine = origin_machine[:32].rjust(32).encode("ascii")
    return MAGIC + b"\x00\x00" + machine + created.encode("ascii") + bytes(256)


def parse_header(data):
    if len(data) < HEADER_SIZE or data[:8] != MAGIC or data[8] != 0:
        raise InventoryError("not an inventory.data archive (bad magic or version)")
    return {"flags": data[9], "origin_machine": data[10:42].decode("ascii").strip(),
            "created_on": data[42:56].decode("ascii")}


def compress(barcode):
    """(prefix, number) as Archive.CompressBarcode does; raises InventoryError for anything but digits."""
    b = str(barcode)
    if not b.isdigit() or not b.isascii():
        raise InventoryError(f"{barcode!r} is not a natural number")
    prefix = len(b) - len(b.lstrip("0"))
    if prefix == len(b):
        prefix -= 1
    value = int(b[prefix:])
    if prefix > 255 or value >= 1 << 64:
        raise InventoryError(f"{barcode!r} is too long")
    return prefix, value


def decompress(prefix, value):
    return "0" * prefix + str(value)


def read(path, chunk_records=1 << 16):
    """(header dict, iterator of (barcode, title_id, status, rental_count)) in file order."""
    f = open(path, "rb")
    head = f.read(HEADER_SIZE)
    try:
        info = parse_header(head)
    except InventoryError:
        f.close()
        raise

    def records():
        with f:
            while True:
                block = f.read(RECORD.size * chunk_records)
                if not block:
                    return
                usable = len(block) - len(block) % RECORD.size
                for p, v, title, status, rents in RECORD.iter_unpack(block[:usable]):
                    yield "0" * p + str(v), title, status, rents
                if usable != len(block):
                    return  # a trailing partial record is ignored, as GetNumberOfRecords() does
    return info, records()


def write(path, records, origin_machine="UPDATESVC01", created_on=None, raw_header=None):
    """Write an archive. `records` must already be in barcode order (ordinal); returns the record count."""
    n = 0
    with open(path, "wb") as f:
        f.write(raw_header if raw_header is not None else header(origin_machine, created_on))
        buf = bytearray()
        for barcode, title, status, rents in records:
            p, v = compress(barcode)
            buf += RECORD.pack(p, v, int(title), int(status), int(rents))
            n += 1
            if len(buf) >= 1 << 22:
                f.write(buf)
                buf.clear()
        f.write(buf)
    return n
