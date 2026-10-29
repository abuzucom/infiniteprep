"""Cover art normalization service for Pioneer XDJ-RX3 baseline JPEG compatibility."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

XDJ_RX3_MAX_ART_DIM: Final[int] = 800
DEFAULT_JPEG_QUALITY: Final[int] = 90


class ArtworkService:
    """Scales and converts cover art images to <= 800x800 baseline RGB JPEG for XDJ-RX3."""

    def normalize_image_bytes(
        self,
        raw_image_bytes: bytes,
        max_dimension: int = XDJ_RX3_MAX_ART_DIM,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ) -> bytes:
        """Decode image bytes, downscale to max_dimension if needed, and encode as RGB JPEG."""
        image = QImage.fromData(raw_image_bytes)
        if image.isNull():
            raise ValueError("Invalid or unsupported cover art image data; check image file.")

        rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
        if rgb_image.width() > max_dimension or rgb_image.height() > max_dimension:
            rgb_image = rgb_image.scaled(
                max_dimension,
                max_dimension,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        rgb_image.save(buffer, "JPG", jpeg_quality)
        buffer.close()
        return bytes(byte_array.data())
