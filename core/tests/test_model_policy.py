"""V7 M1 (#415): SpendPolicy never switches to a paid model silently. Fakes only: no network, no keys."""
import pytest

from jarves.model_policy import ModelInfo, ModelRegistry, SpendPolicy, load_registry

LOCAL = ModelInfo("loc", "local", local=True, paid=False, quality=3)
FREE = ModelInfo("fre", "openrouter", paid=False, quality=5)
CHEAP = ModelInfo("chp", "openai", input_per_1m=1.0, output_per_1m=2.0, quality=6)
BEST = ModelInfo("bst", "openai", input_per_1m=10.0, output_per_1m=20.0, quality=9)
REG = ModelRegistry([LOCAL, FREE, CHEAP, BEST])


def test_default_is_zero_paid():
    p = SpendPolicy()
    d = p.decide(REG, tokens_in=1000, tokens_out=1000)
    assert d.model.id in ("loc", "fre") and not d.model.paid
    d = p.decide(REG, preferred="chp", tokens_in=10)
    assert d.model is None and "paid" in d.exception.reason


def test_unavailable_choice_is_exception_not_silent_paid():
    p = SpendPolicy(mode="balanced", daily_cap_usd=5, task_cap_usd=1)
    d = p.decide(REG, preferred="fre", available={"chp", "bst"}, tokens_in=1000)
    assert d.model is None                       # no hidden switch to chp
    assert d.exception.needs_owner and d.exception.wanted == "fre" and d.exception.proposed == "chp"


def test_free_only_and_local_only_modes():
    assert SpendPolicy(mode="local-only", daily_cap_usd=9, task_cap_usd=9).decide(REG).model.id == "loc"
    d = SpendPolicy(mode="local-only").decide(REG, preferred="fre")
    assert d.model is None and "local-only" in d.exception.reason
    d = SpendPolicy(mode="free-only", daily_cap_usd=9, task_cap_usd=9).decide(REG, preferred="bst")
    assert d.model is None and d.exception.proposed in ("loc", "fre")


def test_modes_pick_order_with_budget():
    kw = dict(daily_cap_usd=100, task_cap_usd=100)
    assert SpendPolicy(mode="best-quality", **kw).decide(REG, tokens_in=1000).model.id == "bst"
    assert SpendPolicy(mode="cheap", **kw).decide(REG).model.paid is False


def test_caps_task_daily_agent():
    p = SpendPolicy(mode="best-quality", daily_cap_usd=1.0, task_cap_usd=0.5, agent_cap_usd={"a": 0.3})
    assert p.decide(REG, preferred="chp", tokens_in=1_000_000).exception.reason.startswith("task estimate")
    assert p.decide(REG, preferred="chp", agent="a", tokens_in=200_000).model.id == "chp"      # $0.20
    p.record("a", 0.2)
    assert "agent a daily cap" in p.decide(REG, preferred="chp", agent="a", tokens_in=200_000).exception.reason
    p.record("b", 0.9)
    assert "daily cap" in p.decide(REG, preferred="chp", agent="b", tokens_in=200_000).exception.reason


def test_project_allow_list_and_privacy_label():
    p = SpendPolicy(mode="balanced", daily_cap_usd=5, task_cap_usd=5, allowed_providers={"priv": ["local"]})
    assert p.decide(REG, preferred="fre", project="priv").model is None
    assert p.decide(REG, preferred="loc", project="priv").model.id == "loc"
    assert "not private" in FREE.privacy_label and "device" in LOCAL.privacy_label


def test_bad_mode_and_unknown_model():
    with pytest.raises(ValueError):
        SpendPolicy(mode="yolo")
    assert SpendPolicy().decide(REG, preferred="nope").exception.reason.startswith("unknown")


def test_registry_file_and_default(tmp_path):
    assert load_registry(tmp_path / "missing.toml").get("gpt-6-luna").paid
    f = tmp_path / "models.toml"
    f.write_text('[[model]]\nid="x"\nprovider="p"\npaid=false\ninput_per_1m=0.0\n', encoding="utf-8")
    assert load_registry(f).get("x").provider == "p" and not load_registry(f).get("x").paid
