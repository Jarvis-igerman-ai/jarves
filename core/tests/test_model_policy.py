"""V7 M1 (#415): SpendPolicy never switches to a paid model silently. Fakes only: no network, no keys."""
import pytest

from jarves.model_policy import ModelInfo, ModelRegistry, SpendPolicy, load_registry

LOCAL = ModelInfo("loc", "local", local=True, paid=False, quality=3)
FREE = ModelInfo("fre", "openrouter", paid=False, quality=5)
CHEAP = ModelInfo("chp", "openai", input_per_1m=1.0, output_per_1m=2.0, quality=6)
BEST = ModelInfo("bst", "openai", input_per_1m=10.0, output_per_1m=20.0, quality=9)
REG = ModelRegistry([LOCAL, FREE, CHEAP, BEST])
ALLOW = {"": ["local", "openrouter", "openai"]}


def test_default_is_zero_paid():
    p = SpendPolicy()
    d = p.decide(REG, tokens_in=1000, tokens_out=1000)
    assert d.model.id in ("loc", "fre") and not d.model.paid
    d = p.decide(REG, preferred="chp", tokens_in=10)
    assert d.model is None and "paid" in d.exception.reason


def test_unavailable_choice_is_exception_not_silent_paid():
    p = SpendPolicy(mode="balanced", daily_cap_usd=5, task_cap_usd=1, allowed_providers=ALLOW)
    d = p.decide(REG, preferred="fre", available={"chp", "bst"}, tokens_in=1000)
    assert d.model is None                       # no hidden switch to chp
    assert d.exception.needs_owner and d.exception.wanted == "fre" and d.exception.proposed == "chp"


def test_free_only_and_local_only_modes():
    assert SpendPolicy(mode="local-only", daily_cap_usd=9, task_cap_usd=9, allowed_providers=ALLOW).decide(REG).model.id == "loc"
    d = SpendPolicy(mode="local-only").decide(REG, preferred="fre")
    assert d.model is None and "local-only" in d.exception.reason
    d = SpendPolicy(mode="free-only", daily_cap_usd=9, task_cap_usd=9).decide(REG, preferred="bst")
    assert d.model is None and d.exception.proposed in ("loc", "fre")


def test_modes_pick_order_with_budget():
    kw = dict(daily_cap_usd=100, task_cap_usd=100, allowed_providers=ALLOW)
    assert SpendPolicy(mode="best-quality", **kw).decide(REG, tokens_in=1000).model.id == "bst"
    assert SpendPolicy(mode="cheap", **kw).decide(REG).model.paid is False


def test_caps_task_daily_agent():
    p = SpendPolicy(mode="best-quality", daily_cap_usd=1.0, task_cap_usd=0.5, agent_cap_usd={"a": 0.3}, allowed_providers=ALLOW)
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
    f.write_text('[[model]]\nid="x"\nprovider="p"\ninput_per_1m=1.0\noutput_per_1m=2.0\n', encoding="utf-8")
    assert load_registry(f).get("x").provider == "p" and load_registry(f).get("x").paid


# ---- fail-closed review fixes ----
import math
import threading

from jarves.model_policy import ModelConfigError


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "5", True, None])
def test_bad_numbers_rejected(bad):
    with pytest.raises(ModelConfigError):
        SpendPolicy(daily_cap_usd=bad)
    with pytest.raises(ModelConfigError):
        SpendPolicy(task_cap_usd=bad)
    with pytest.raises(ModelConfigError):
        SpendPolicy(agent_cap_usd={"a": bad})
    with pytest.raises(ModelConfigError):
        ModelInfo("m", "p", input_per_1m=bad, output_per_1m=1.0)
    with pytest.raises(ModelConfigError):
        SpendPolicy().record("a", bad)


def test_negative_record_cannot_lower_spend():
    p = SpendPolicy(daily_cap_usd=1)
    p.record("a", 0.5)
    with pytest.raises(ModelConfigError):
        p.record("a", -0.4)
    assert p.spent_today == 0.5


def test_bad_tokens_refused():
    d = SpendPolicy().decide(REG, tokens_in=float("nan"))
    assert d.model is None and d.exception
    assert SpendPolicy().decide(REG, tokens_in=-5).model is None


def test_unpriced_paid_model_is_blocked():
    reg = ModelRegistry([ModelInfo("np", "openai"), ModelInfo("zp", "openai", input_per_1m=0.0, output_per_1m=0.0)])
    p = SpendPolicy(mode="best-quality", daily_cap_usd=100, task_cap_usd=100, allowed_providers={"": ["openai"]})
    for mid in ("np", "zp"):
        d = p.decide(reg, preferred=mid, tokens_in=1000)
        assert d.model is None and "no known price" in d.exception.reason
    assert p.decide(reg).model is None


def test_free_model_with_price_rejected():
    with pytest.raises(ModelConfigError):
        ModelInfo("m", "p", paid=False, input_per_1m=1.0)


def test_toml_cannot_flip_to_free_or_local(tmp_path):
    f = tmp_path / "models.toml"
    f.write_text('[[model]]\nid="gpt-6-sol"\nprovider="openai"\npaid=false\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)
    f.write_text('[[model]]\nid="new"\nprovider="p"\npaid=false\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)
    assert load_registry(f, free_overrides=["new"]).get("new").paid is False
    f.write_text('[[model]]\nid="free-remote"\nprovider="openrouter"\nlocal=true\npaid=false\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)
    f.write_text('[[model]]\nid="free-remote"\nprovider="evil"\npaid=false\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)


def test_toml_types_checked(tmp_path):
    f = tmp_path / "models.toml"
    f.write_text('[[model]]\nid="x"\nprovider="p"\ninput_per_1m="2"\noutput_per_1m=1\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)
    f.write_text('[[model]]\nid="x"\nprovider="p"\nbogus=1\n', encoding="utf-8")
    with pytest.raises(ModelConfigError):
        load_registry(f)


def test_project_without_allow_list_is_local_or_free_only():
    p = SpendPolicy(mode="best-quality", daily_cap_usd=100, task_cap_usd=100)
    d = p.decide(REG, preferred="bst", project="unlisted", tokens_in=10)
    assert d.model is None and "no allow-list" in d.exception.reason
    assert p.decide(REG, project="unlisted").model.paid is False


def test_string_allow_list_rejected_no_substring_match():
    with pytest.raises(ModelConfigError):
        SpendPolicy(allowed_providers={"priv": "local"})
    p = SpendPolicy(mode="balanced", daily_cap_usd=5, task_cap_usd=5)
    p.allowed_providers["priv"] = "local"          # mutated after construction: still refused
    assert p.decide(REG, preferred="loc", project="priv").model is None


def test_mode_validated_after_construction():
    p = SpendPolicy()
    with pytest.raises(ModelConfigError):
        p.mode = "yolo"
    assert p.mode == "free-only"
    p.mode = "cheap"
    assert p.mode == "cheap"
    p._mode = "yolo"                                # even a forced bad value fails closed
    assert p.decide(REG, preferred="loc").model is None


def test_concurrent_record_is_exact():
    p = SpendPolicy(daily_cap_usd=1e9)
    def work():
        for _ in range(2000):
            p.record("a", 0.25)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert p.spent_today == 8 * 2000 * 0.25 and p.spent_by_agent["a"] == p.spent_today
    assert math.isfinite(p.spent_today)
