"""Pin the Opus TOC bytes from tests/fixtures/synthetic_audio.py to RFC 6716 section 3.1.

The TOC byte holds config in the top five bits, the stereo flag s in bit 2, and the frame
count code c in the low two bits. Config 31 is CELT fullband with 20 ms frames.
"""

from __future__ import annotations

import pytest

from tests.fixtures import synthetic_audio as sa

RFC6716_CONFIG_SHIFT = 3
RFC6716_STEREO_BIT = 0x04
RFC6716_CODE_MASK = 0x03
CELT_FULLBAND_20MS_CONFIG = 31


@pytest.mark.parametrize(("channels", "stereo_flag"), [(1, 0), (2, RFC6716_STEREO_BIT)])
def test_opus_packets_carry_rfc6716_toc(channels: int, stereo_flag: int) -> None:
    """Each one-byte packet decodes to config 31, the stereo flag for channels, and code 0."""
    data = sa.build_ogg_opus_bytes(duration_sec=0.1, channels=channels, seed=1)
    # The last page holds only audio packets of one byte, so the file ends in a TOC byte.
    toc = data[-1]
    assert toc >> RFC6716_CONFIG_SHIFT == CELT_FULLBAND_20MS_CONFIG
    assert toc & RFC6716_STEREO_BIT == stereo_flag
    assert toc & RFC6716_CODE_MASK == 0
    assert toc == (0xFC if channels == 2 else 0xF8)
