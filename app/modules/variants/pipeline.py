"""The image pipeline: pure functions on bytes, no I/O, so the worker runs it in a thread and the
tests call it directly."""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

CONTENT_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_PIL_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp"}


class ImageRejectedError(Exception):
    """The file is not acceptable. Final: retrying cannot change the answer."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RenderedVariant:
    name: str
    data: bytes
    content_type: str
    width: int
    height: int


@dataclass(frozen=True)
class ProcessedImage:
    format: str
    width: int
    height: int
    variants: list[RenderedVariant]


def detect_format(data: bytes) -> str | None:
    """The real type, from the file's first bytes ("magic numbers"). The filename and the
    Content-Type header are chosen by the client and prove nothing."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def fit_within(width: int, height: int, longest_side: int) -> tuple[int, int]:
    """Scales down so the longer side is at most `longest_side`, keeping the aspect ratio.
    Never scales up."""
    if max(width, height) <= longest_side:
        return width, height
    scale = longest_side / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode(image: Image.Image, fmt: str, icc_profile: bytes | None) -> bytes:
    if fmt == "jpeg" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if fmt == "webp" and image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    buffer = BytesIO()
    options: dict[str, object] = {"quality": 85} if fmt in ("jpeg", "webp") else {}
    if icc_profile:
        options["icc_profile"] = icc_profile
    image.save(buffer, _PIL_FORMATS[fmt], **options)
    return buffer.getvalue()


def process_image(
    data: bytes, declared_content_type: str, max_pixels: int, sizes: dict[str, int]
) -> ProcessedImage:
    """Checks the file, then returns the sanitised original plus one resized copy per size.

    Order matters: type check, then pixel count from the header, and only then decoding. A
    decompression bomb (a small file that decodes to gigabytes) is refused before any pixel is
    decoded.
    """
    fmt = detect_format(data)
    if fmt is None:
        raise ImageRejectedError("unsupported_type: not a JPEG, PNG or WebP file")
    if CONTENT_TYPES[fmt] != declared_content_type:
        raise ImageRejectedError(
            f"type_mismatch: declared {declared_content_type} but the file is {CONTENT_TYPES[fmt]}"
        )

    try:
        with Image.open(BytesIO(data), formats=[_PIL_FORMATS[fmt]]) as opened:
            width, height = opened.size  # read from the header; nothing decoded yet
            if width * height > max_pixels:
                raise ImageRejectedError(
                    f"too_many_pixels: {width}x{height} is over the {max_pixels} pixel limit"
                )
            opened.load()
            icc_profile = opened.info.get("icc_profile")
            # Apply the EXIF orientation first, so dropping the metadata does not rotate photos.
            upright = ImageOps.exif_transpose(opened)
    except Image.DecompressionBombError as exc:
        # Pillow's own guard (beyond twice its default limit) can fire while opening.
        raise ImageRejectedError(f"too_many_pixels: {exc}") from exc
    except ImageRejectedError:
        raise
    except (OSError, SyntaxError, ValueError) as exc:
        raise ImageRejectedError(f"undecodable: {exc}") from exc

    # A brand-new image from the pixels alone: EXIF (with GPS), XMP and text chunks stay behind.
    # Only the ICC colour profile is carried over, because it changes how colours look.
    clean = Image.frombytes(upright.mode, upright.size, upright.tobytes())
    content_type = CONTENT_TYPES[fmt]
    variants = [
        RenderedVariant(
            "original", _encode(clean, fmt, icc_profile), content_type, clean.width, clean.height
        )
    ]
    for name, longest_side in sizes.items():
        resized = clean.resize(
            fit_within(clean.width, clean.height, longest_side), Image.Resampling.LANCZOS
        )
        variants.append(
            RenderedVariant(
                name,
                _encode(resized, fmt, icc_profile),
                content_type,
                resized.width,
                resized.height,
            )
        )
    return ProcessedImage(format=fmt, width=clean.width, height=clean.height, variants=variants)
