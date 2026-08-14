import base64

import pytest
from outpost import fallback as fb

LINKS = [
    "vless://uuid@203.0.113.1:443?security=reality#one",
    "trojan://pw@203.0.113.2:8443#two",
    "ss://YWVzLTI1Ni1nY206cGFzcw==@203.0.113.3:8388#three",
]
PLAIN = LINKS[0] + "\nsome junk line\n" + LINKS[1] + "\n"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def test_extract_links_plain_drops_junk():
    assert fb.extract_links(PLAIN) == [LINKS[0], LINKS[1]]


def test_extract_links_base64_body():
    assert fb.extract_links(_b64(PLAIN)) == [LINKS[0], LINKS[1]]


def test_extract_links_garbage_is_empty():
    assert fb.extract_links("<html>blocked by DPI</html>") == []
    assert fb.extract_links("") == []


def test_build_bundles_merges_and_dedupes():
    def fake(fname):
        if fname.startswith(("BLACK", "WHITE")):
            # both files of each bundle share LINKS[0]; it must appear once
            return "\n".join(
                [LINKS[0], LINKS[1]] if "VLESS" in fname or "CIDR" in fname else [LINKS[0], LINKS[2]]
            )
        raise AssertionError(fname)

    bundles = fb.build_bundles(fetch=fake)
    for key in ("fallback", "fallback-white"):
        body = base64.b64decode(bundles[key]).decode()
        assert body.splitlines() == [LINKS[0], LINKS[1], LINKS[2]]


def test_build_bundles_survives_partial_file_failure():
    def fake(fname):
        if fname == "BLACK_VLESS_RUS_mobile.txt":
            raise fb.FallbackError("mirror down")
        return LINKS[0]

    bundles = fb.build_bundles(fetch=fake)
    assert base64.b64decode(bundles["fallback"]).decode() == LINKS[0]


def test_build_bundles_refuses_empty_bundle():
    def fake(fname):
        if fname.startswith("WHITE"):
            raise fb.FallbackError("all mirrors down")
        return LINKS[0]

    # an empty bundle must never silently replace a working KV entry
    with pytest.raises(fb.FallbackError, match="fallback-white"):
        fb.build_bundles(fetch=fake)


def test_sync_writes_bundle_files(tmp_path):
    written = fb.sync(tmp_path, fetch=lambda fname: PLAIN)
    assert set(written) == {"fallback", "fallback-white"}
    for key, path in written.items():
        assert path == tmp_path / f"outpost.{key}.txt"
        assert base64.b64decode(path.read_text()).decode().splitlines() == [LINKS[0], LINKS[1]]
