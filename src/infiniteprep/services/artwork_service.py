"""Cover art normalization service with decompression-bomb protection for Pioneer XDJ-RX3."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader

XDJ_RX3_MAX_ART_DIM: Final[int] = 800
DEFAULT_JPEG_QUALITY: Final[int] = 90
MAX_ARTWORK_BYTES: Final[int] = 16_777_216  # 16 MiB raw input cap
MAX_DECODE_DIMENSION: Final[int] = 4096
MAX_IMAGE_PIXELS: Final[int] = 16_777_216  # 4096 x 4096 maximum pixel count
QT_ALLOCATION_LIMIT_MB: Final[int] = 64


class ArtworkService:
    """Scales and converts cover art images to <= 800x800 baseline RGB JPEG for XDJ-RX3."""

    def normalize_image_bytes(
        self,
        raw_image_bytes: bytes,
        max_dimension: int = XDJ_RX3_MAX_ART_DIM,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ) -> bytes:
        """Validate header bounds, decode image, downscale to max_dimension, and encode JPEG."""
        if not raw_image_bytes or len(raw_image_bytes) > MAX_ARTWORK_BYTES:
            raise ValueError(
                f"Cover art payload size ({len(raw_image_bytes)} bytes) is empty or exceeds "
                f"safety limit ({MAX_ARTWORK_BYTES} bytes)."
            )
        image = self._safe_decode_qimage(raw_image_bytes)
        rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
        if rgb_image.width() > max_dimension or rgb_image.height() > max_dimension:
            rgb_image = rgb_image.scaled(
                max_dimension,
                max_dimension,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        out_bytes = QByteArray()
        out_buffer = QBuffer(out_bytes)
        out_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        rgb_image.save(out_buffer, "JPG", jpeg_quality)
        out_buffer.close()
        return bytes(out_bytes.data())

    def _safe_decode_qimage(self, raw_image_bytes: bytes) -> QImage:
        """Inspect image header dimensions via QImageReader before allocating pixel buffer."""
        QImageReader.setAllocationLimit(QT_ALLOCATION_LIMIT_MB)
        in_array = QByteArray(raw_image_bytes)
        in_buffer = QBuffer(in_array)
        in_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(in_buffer)
        size = reader.size()
        if size.isValid():
            width, height = size.width(), size.height()
            if (
                width <= 0
                or height <= 0
                or width > MAX_DECODE_DIMENSION
                or height > MAX_DECODE_DIMENSION
                or (width * height) > MAX_IMAGE_PIXELS
            ):
                in_buffer.close()
                raise ValueError(
                    f"Image dimensions ({width}x{height}) exceed decompression-bomb safety limits."
                )
        image = reader.read()
        in_buffer.close()
        if image.isNull():
            raise ValueError("Invalid or unsupported cover art image data; check image file.")
        if (
            image.width() > MAX_DECODE_DIMENSION
            or image.height() > MAX_DECODE_DIMENSION
            or (image.width() * image.height()) > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Decoded image exceeds maximum pixel count safety limit.")
        return image
