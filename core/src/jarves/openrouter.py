"""OpenRouter provider adapter (V7 slice M2, issue #416). Fake-first: no test, CI job or default path calls the real API.

Rules (a safety control; changes need independent review):
* OFF by default (feature `provider_openrouter`, beta, high risk). HTTPS to openrouter.ai ONLY; no redirects are followed.
* The key is OPENROUTER_API_KEY from the existing secret store (secrets.env). Never logged, never in an error text.
* Every call goes through `SpendPolicy.decide` first. In `free-only` mode ONLY a registry-free model (price 0) whose id
  ends in `:free` (or the `openrouter/free` router) is allowed; anything else raises PolicyBlocked. No silent paid call.
* The cost of every run comes from the response usage and is recorded in the policy. A charge seen in free-only mode is
  recorded and reported as OPENROUTER_UNEXPECTED_CHARGE.
* Remote inference is NOT private. Content marked `private` or `code` is refused unless the project's allow-list names
  `openrouter` explicitly (a project without an allow-list never sends it). Unknown content classes are refused.
* Errors map to codes: OPENROUTER_NOT_CONNECTED, _AUTH (401), _CREDITS (402), _RATE_LIMIT (429), _BAD_REQUEST,
  _OFFLINE (timeout or no connection), _PROVIDER_ERROR, _REDIRECT, _PRIVACY, _UNEXPECTED_CHARGE.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import clilang
from . import features as feat
from .channels_common import targets
from .model_policy import DEFAULT_MODELS, MODES, ModelConfigError, ModelRegistry, PolicyException, SpendPolicy, _num

HOST = "openrouter.ai"
API = f"https://{HOST}/api/v1"
FREE_ROUTER = "openrouter/free"
KEY_ENV = "OPENROUTER_API_KEY"
START_COMMAND = "jarvis models key"
TIMEOUT = 60.0
MAX_BODY = 2_000_000
CLASSES = ("public", "private", "code")
_KEY = re.compile(r"(sk-or-[A-Za-z0-9_-]+|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,})")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}$")


def redact(text: object) -> str:
    return _KEY.sub("[REDACTED]", str(text))


class OpenRouterError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(redact(message))
        self.code, self.message = code, redact(message)


class PolicyBlocked(Exception):
    """The spend policy or the privacy guard refused the call. Carries the owner request; nothing was sent."""

    def __init__(self, exc: PolicyException) -> None:
        super().__init__(exc.reason)
        self.exception = exc


def is_free_id(model_id: str) -> bool:
    return model_id == FREE_ROUTER or model_id.endswith(":free")


@dataclass(frozen=True)
class Result:
    text: str
    requested_model: str
    resolved_model: str          # logged for every run: the free router picks a model at random
    tokens_in: int
    tokens_out: int
    cost_usd: float
    privacy: str                 # always "remote: ..." for this provider


def _tokens(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _cost(v) -> float:
    try:
        return _num(v, "cost")
    except ModelConfigError:
        return 0.0


def parse_response(body: dict, requested: str) -> Result:
    """Documented shape: {"model", "choices":[{"message":{"content"}}], "usage":{"prompt_tokens","completion_tokens","cost"}}.
    A 200 can also carry {"error": {...}} (upstream failure): that is an error, not an empty answer."""
    if not isinstance(body, dict):
        raise OpenRouterError("OPENROUTER_PROVIDER_ERROR", "unexpected answer shape")
    err = body.get("error")
    if err:
        raise _map(int(err.get("code") or 502) if isinstance(err, dict) and isinstance(err.get("code"), int) else 502, {})
    try:
        text = body["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise OpenRouterError("OPENROUTER_PROVIDER_ERROR", "unexpected answer shape") from None
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    model = body.get("model")
    return Result(str(text), requested, model if isinstance(model, str) and model else requested,
                  _tokens(u.get("prompt_tokens")), _tokens(u.get("completion_tokens")), _cost(u.get("cost")),
                  "remote: prompts leave this device (not private)")


def _map(status: int, headers) -> OpenRouterError:
    if status == 401:
        return OpenRouterError("OPENROUTER_AUTH", "OpenRouter rejected the key (missing, expired or revoked)")
    if status == 402:
        return OpenRouterError("OPENROUTER_CREDITS", "OpenRouter says the account has no credits for this model")
    if status == 429:
        ra = headers.get("retry-after", "") if hasattr(headers, "get") else ""
        return OpenRouterError("OPENROUTER_RATE_LIMIT", "rate limit (free models are limited)" + (f"; retry after {ra}s" if ra else ""))
    if status in (400, 404, 422):
        return OpenRouterError("OPENROUTER_BAD_REQUEST", f"OpenRouter refused the request ({status})")
    if status == 408:
        return OpenRouterError("OPENROUTER_OFFLINE", "OpenRouter timed out")
    return OpenRouterError("OPENROUTER_PROVIDER_ERROR", f"OpenRouter answered {status}")


class OpenRouterClient:
    """`base` other than https://openrouter.ai is for tests only (`_test_base=True`); a real config can never set it."""

    def __init__(self, policy: SpendPolicy, registry: ModelRegistry | None = None, *, key: str = "",
                 transport: httpx.BaseTransport | None = None, base: str = API, _test_base: bool = False) -> None:
        u = urlsplit(base)
        if not _test_base and (u.scheme != "https" or u.hostname != HOST or u.port not in (None, 443) or u.username):
            raise OpenRouterError("OPENROUTER_BAD_REQUEST", "only https://openrouter.ai is allowed")
        self.policy, self.registry = policy, registry or ModelRegistry(DEFAULT_MODELS)
        self._key, self.transport, self.base = key, transport, base.rstrip("/")

    def __repr__(self) -> str:
        return "OpenRouterClient(key=[REDACTED])"

    def _check(self, model: str, agent: str, project: str, content_class: str, tin: int, tout: int):
        if content_class not in CLASSES:
            raise PolicyBlocked(PolicyException(f"unknown content class {str(content_class)[:20]!r}: refused", model))
        if content_class != "public" and "openrouter" not in (self.policy.allowed_providers.get(project) or ()):
            raise PolicyBlocked(PolicyException(
                f"{content_class} content never goes to OpenRouter unless project {project or '(none)'} lists it", model))
        m = self.registry.get(model) if isinstance(model, str) and _MODEL.match(model) else None
        if m is None or m.provider != "openrouter":
            raise PolicyBlocked(PolicyException(f"{str(model)[:60]!r} is not an OpenRouter model in the registry", str(model)[:60]))
        if self.policy.mode == "free-only" and (m.paid or not is_free_id(m.id)):
            raise PolicyBlocked(PolicyException("free-only mode: only price-0 models ending in :free are allowed", m.id))
        d = self.policy.decide(self.registry, preferred=m.id, agent=agent, project=project, tokens_in=tin, tokens_out=tout)
        if d.model is None:
            raise PolicyBlocked(d.exception)
        return d.model

    def chat(self, messages: list[dict], model: str = FREE_ROUTER, *, agent: str = "default", project: str = "",
             content_class: str = "private", max_tokens: int = 1024) -> Result:
        if not isinstance(messages, list) or not messages or not all(
                isinstance(x, dict) and isinstance(x.get("content"), str) and x.get("role") in ("system", "user", "assistant")
                for x in messages):
            raise OpenRouterError("OPENROUTER_BAD_REQUEST", "messages must be a list of {role, content}")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8192:
            raise OpenRouterError("OPENROUTER_BAD_REQUEST", "max_tokens must be 1..8192")
        tin = sum(len(x["content"]) for x in messages) // 3 + 1          # rough upper estimate, only for the policy caps
        m = self._check(model, agent, project, content_class, tin, max_tokens)
        key = self._key or os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise OpenRouterError("OPENROUTER_NOT_CONNECTED", f"OpenRouter is not connected; run {START_COMMAND}")
        body = {"model": m.id, "messages": [{"role": x["role"], "content": x["content"]} for x in messages],
                "max_tokens": max_tokens, "usage": {"include": True}}
        if self.policy.mode == "free-only":       # price ceiling 0 BEFORE the call: the router can never pick a paid model
            body["provider"] = {"max_price": {"prompt": 0, "completion": 0}, "allow_fallbacks": False}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "JARVES"}
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=False) as c:
                r = c.post(self.base + "/chat/completions", headers=headers, content=json.dumps(body))
        except httpx.HTTPError as e:
            raise OpenRouterError("OPENROUTER_OFFLINE", f"cannot reach OpenRouter ({type(e).__name__})") from None
        if 300 <= r.status_code < 400:
            raise OpenRouterError("OPENROUTER_REDIRECT", "OpenRouter tried to redirect; not followed")
        if r.status_code >= 400:
            raise _map(r.status_code, r.headers)
        if len(r.content) > MAX_BODY:
            raise OpenRouterError("OPENROUTER_PROVIDER_ERROR", "answer too large")
        try:
            res = parse_response(r.json(), m.id)
        except ValueError:
            raise OpenRouterError("OPENROUTER_PROVIDER_ERROR", "OpenRouter sent an unreadable answer") from None
        est = m.cost_usd(res.tokens_in, res.tokens_out)       # never trust a lower reported cost for a priced model
        if math.isfinite(est) and est > res.cost_usd:
            res = dataclasses.replace(res, cost_usd=est)
        if res.cost_usd:
            self.policy.record(agent, res.cost_usd)
        if self.policy.mode == "free-only" and (res.cost_usd > 0 or not is_free_id(res.resolved_model)):
            raise OpenRouterError("OPENROUTER_UNEXPECTED_CHARGE",
                                  f"free-only run resolved to {res.resolved_model[:60]} with cost ${res.cost_usd:.6f}; recorded")
        return res


# ------------------------------------------------------------------------------------------ feature + CLI
def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    from .config import load_secrets
    load_secrets()
    if not os.environ.get(KEY_ENV, "").strip():
        return False, f"OpenRouter key missing; tell the user: \"תריץ במחשב {START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register("provider_openrouter", "ספק OpenRouter (מודלים חינמיים)", "OpenRouter provider (free models)", False,
              probe=_probe, needs=["openrouter_key"], risk="high",
              about="send non-private text to free OpenRouter models under a hard spend policy (free-only by default); "
                    "private code and data never go there unless the project allows it",
              targets=targets("UNIT test_openrouter.py (fake OpenRouter server on 127.0.0.1, no network, no key)",
                              "never connected to the real OpenRouter; the owner creates the key"),
              permissions=["action"], settings=START_COMMAND)


def add_cli(sub) -> None:
    h = sub.add_parser("models", help="Models (read only): `list` shows the registry with prices and privacy; "
                                      "`status` shows the spend mode and whether the OpenRouter key is saved; `key` saves the key "
                                      "(asked privately); `key-off` deletes it. Switch on: `jarvis features on provider_openrouter`")
    h.add_argument("action", choices=["list", "status", "key", "key-off"])
    h.add_argument("--json", action="store_true")


def cmd_models(a, cfg, home) -> int:
    reg, pol = ModelRegistry(DEFAULT_MODELS), SpendPolicy()
    from .config import load_secrets, save_secret, secrets_file
    if a.action == "key":
        import getpass
        try:
            save_secret(KEY_ENV, getpass.getpass("OpenRouter API key (hidden): "), home)
        except (ValueError, EOFError, KeyboardInterrupt):
            print("OPENROUTER_BAD_REQUEST: no valid key entered")
            return 1
        print(clilang.bi("המפתח נשמר במחשב הזה בלבד. / The key was saved on this computer only."))
        return 0
    if a.action == "key-off":
        f = secrets_file(home)
        if f.exists():
            keep = [l for l in f.read_text(encoding="utf-8-sig").splitlines() if l.partition("=")[0].strip() != KEY_ENV]
            f.write_text(chr(10).join(keep) + chr(10), encoding="utf-8")
        os.environ.pop(KEY_ENV, None)
        print(clilang.bi("מפתח OpenRouter נמחק. / The OpenRouter key was deleted."))
        return 0
    load_secrets(home)
    if a.action == "list":
        rows = reg.view()
    else:
        rows = {"mode": pol.mode, "modes": list(MODES), "paid_allowed": pol.daily_cap_usd > 0,
                "openrouter_key_saved": bool(os.environ.get(KEY_ENV, "").strip()), "remote_is_private": False}
    if getattr(a, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif a.action == "list":
        for r in rows:
            print(f"{r['id']}  [{r['provider']}]  {'free' if not r['paid'] else 'paid'}  {r['privacy']}")
    else:
        print(clilang.bi(f"מצב: {rows['mode']}; מפתח OpenRouter שמור: {rows['openrouter_key_saved']}. / "
                         f"mode: {rows['mode']}; OpenRouter key saved: {rows['openrouter_key_saved']}."))
    return 0
