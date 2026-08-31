from __future__ import annotations

import pytest

from obsion_im.config import ImError, require_local_delivery
from obsion_im.envelopes import parse_inbound
from obsion_im.webhook import parse_listen_bind


def test_wecom_ciphertext_is_rejected_and_listen_stays_loopback() -> None:
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_inbound("wecom", {"Encrypt": "cipher"})
    with pytest.raises(ImError, match="127.0.0.1"):
        parse_listen_bind("0.0.0.0:8787")
    with pytest.raises(ImError, match="HTTP delivery is not implemented"):
        require_local_delivery("http")
