"""Test images, built in code so the repository holds no binary fixtures."""

import struct
import zlib
from io import BytesIO

from PIL import ExifTags, Image


def jpeg_with_gps(width: int = 40, height: int = 20, *, rotate_tag: bool = True) -> bytes:
    """A JPEG with camera make, GPS position and (optionally) an orientation tag saying
    "rotate 90 degrees": what a phone photo typically carries."""
    image = Image.new("RGB", (width, height), (200, 30, 30))
    for x in range(width):
        image.putpixel((x, 0), (0, 0, 255))  # a blue top row, to see the rotation
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestPhone"
    if rotate_tag:
        exif[ExifTags.Base.Orientation] = 6  # rotate 90 clockwise to display
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (51.0, 30.0, 7.5)
    gps[ExifTags.GPS.GPSLongitudeRef] = "W"
    gps[ExifTags.GPS.GPSLongitude] = (0.0, 7.0, 39.0)
    buffer = BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def png(width: int = 30, height: int = 30, color: tuple[int, int, int] = (10, 120, 60)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def disguised_pdf() -> bytes:
    """Not an image at all, though the client will call it image/jpeg."""
    return b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def png_bomb(width: int = 30_000, height: int = 30_000) -> bytes:
    """A PNG whose header claims width x height pixels (900 million by default), with almost no
    pixel data. Decoding it would need gigabytes; it is a few dozen bytes on disk."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + chunk(b"IEND", b"")
    )
