from outpost.models import Provider, Region, Registry


def _provider(name, policy_ok, regions):
    return Provider(name=name, policy_ok=policy_ok, regions=regions)


def test_ru_region_not_eligible():
    p = _provider(
        "aeza",
        True,
        [
            Region(code="ru", country="RU", enabled=True),
            Region(code="nl", country="NL", enabled=True),
        ],
    )
    codes = [r.code for r in p.eligible_regions()]
    assert codes == ["nl"]


def test_policy_blocked_provider_has_no_eligible_regions():
    p = _provider("hetzner", False, [Region(code="de", country="DE", enabled=True)])
    assert p.eligible_regions() == []


def test_disabled_region_excluded():
    p = _provider("zomro", True, [Region(code="nl", country="NL", enabled=False)])
    assert p.eligible_regions() == []


def test_registry_eligible_pairs():
    reg = Registry(
        providers=[
            _provider("aeza", True, [Region(code="nl", country="NL", enabled=True)]),
            _provider("hetzner", False, [Region(code="de", country="DE", enabled=True)]),
        ]
    )
    pairs = reg.eligible()
    assert len(pairs) == 1
    assert pairs[0][0].name == "aeza"
