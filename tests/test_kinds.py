"""Market kinds from installed packages, through the entry point group."""

from dataclasses import dataclass

from crowdbook_arena import kinds as kinds_module


@dataclass(frozen=True)
class Extra:
    name: str = "extra"
    title: str = "Extra"
    description: str = "a kind from a plug-in"

    def scenario(self, seed, duration_s):
        return "x"


class FakeEntryPoint:
    def __init__(self, name, obj):
        self.name, self._obj = name, obj

    def load(self):
        return self._obj


def test_plugins_add_kinds_and_bad_ones_are_skipped(monkeypatch):
    eps = [FakeEntryPoint("one", Extra()), FakeEntryPoint("many", lambda: [Extra(name="more")]),
           FakeEntryPoint("broken", object()), FakeEntryPoint("clash", Extra(name="calm"))]
    monkeypatch.setattr(kinds_module, "entry_points", lambda group: eps)
    found = kinds_module.load_kinds()
    assert {"calm", "news", "trending", "pair", "basket", "extra", "more"} == set(found)
    assert found["calm"].title == "Sea Salt"  # a plug-in cannot replace a kind
    assert set(kinds_module.load_kinds(("extra", "calm"))) == {"extra", "calm"}
