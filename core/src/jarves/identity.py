"""Identity & voice (SW-V2 spec 6.3, D12, D18): the assistant's display name, spoken name, wake phrase and voice.

Three separate things (never conflated):
- display name   written forms: dashboard, logs, window titles (cfg.assistant_name)
- spoken name    how he says his own name in Hebrew speech (cfg.assistant_name_spoken)
- wake phrase    what the local detector listens for. The built-in "hey_jarvis" model, or a phrase trained on this
                 device with `jarvis wake train` (voice/wake_train.py). Renaming does NOT retrain the detector: the
                 dashboard and the rename tool say which phrase still wakes him.

Persisted in <state_dir>/identity.json (atomic) and applied over config.toml at Core start (`apply_to_config`); a
running Core also picks up names live (next conversation). The Live voice stays in memory.db
`override:voice.voice` (shared with the settings Skill's set_voice).

Voice choice rules (D12):
- same provider + model (e.g. another GPT-Live voice): no cost change -> allowed from the dashboard or by speech.
- another voice model (GPT-Live <-> gpt-realtime): price and session behaviour change -> the dashboard needs an
  explicit `approve_cost`; by speech it is refused (the dashboard shows the request).
- an unknown/unsupported stored voice falls back to the default with a warning (never a broken session).
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

from . import capabilities, locales, logs

FILE = "identity.json"
DEFAULT_WAKE = {"model": "hey_jarvis", "phrase": "Hey Jarvis", "threshold": None}
# gpt-realtime voices, official list checked 2026-10-19 (developers.openai.com/api/docs/guides/realtime-conversations:
# "For best quality, we recommend using marin or cedar").
REALTIME_VOICES = {"marin": "recommended (best quality)", "cedar": "recommended (best quality), current default",
                   "alloy": "neutral", "ash": "masculine", "ballad": "masculine, expressive", "coral": "feminine",
                   "echo": "masculine", "sage": "feminine", "shimmer": "feminine", "verse": "masculine"}
PREVIEW_NOTE = ("No preview: OpenAI publishes no sample clips through the API, and JARVES does not fake one. Hearing a "
                "voice needs a short real session (about $0.05 per minute on GPT-Live).")
_NAME = re.compile(r"^[\w֐-׿][\w֐-׿ '\".\-]{0,39}$")


class IdentityError(ValueError):
    pass


def _clean_name(v: str, what: str) -> str:
    v = " ".join(str(v or "").split())
    if not _NAME.match(v):
        raise IdentityError(f"{what} must be 1-40 letters (Hebrew or English), spaces, ' \" . - only")
    return v


def _wake_capability() -> dict:
    """capabilities.WAKE_PHRASE as a copy with Hebrew evidence / blocker next to the English ones."""
    cap = copy.deepcopy(capabilities.WAKE_PHRASE)
    for group in (cap["targets"], cap["custom"], cap["recorder"]):
        for t in group.values():
            locales.attach(t, "evidence", "blocker")
    return cap


def voice_catalog(cfg) -> list[dict]:
    """Every voice JARVES can use, with provider, model, languages, price and honest preview status."""
    from .skills.settings import LIVE_VOICES
    prices = getattr(getattr(cfg, "pricing", None), "voice_per_minute", {}) or {}
    v = cfg.voice
    out = []
    for mode, model, voices in (("live", v.live_model, LIVE_VOICES), ("realtime", v.realtime_model, REALTIME_VOICES)):
        for name, about in voices.items():
            out.append({"id": f"{mode}:{name}", "mode": mode, "provider": "OpenAI", "model": model, "voice": name,
                        "about": about, "languages": "speaks every language the model speaks (Hebrew included); the "
                        "description is its accent", "usd_per_minute": prices.get(model),
                        "cost_kind": "estimate" if mode == "realtime" else "list price",
                        "credential": "OPENAI_API_KEY", "preview": None, "preview_note": PREVIEW_NOTE})
            locales.attach(out[-1], "about", "languages", "preview_note")
    return out


class Identity:
    def __init__(self, home: Path, cfg=None, memory=None) -> None:
        if cfg is None:
            from .config import Config
            cfg = Config()
        self.home, self.cfg, self.memory = home, cfg, memory
        self.path = home / FILE
        self.defaults = {"display_name": cfg.assistant_name, "spoken_name": cfg.assistant_name_spoken}

    # --- storage
    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, d: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _change(self, by: str, **fields) -> dict:
        d = self._read()
        d.update(fields)
        d.setdefault("changed", {})
        for k in fields:
            d["changed"][k] = {"by": by, "ts": time.time()}
        self._write(d)
        return d

    # --- reads
    def names(self) -> dict:
        d = self._read()
        return {"display_name": d.get("display_name") or self.defaults["display_name"],
                "spoken_name": d.get("spoken_name") or self.defaults["spoken_name"]}

    def wake(self) -> dict:
        w = {**DEFAULT_WAKE, **(self._read().get("wake") or {})}
        if w["model"] != "hey_jarvis" and not Path(w["model"]).is_file():
            logs.warn("identity", "WAKE_MODEL_MISSING", "custom wake model missing; back to Hey Jarvis",
                      model=Path(w["model"]).name)
            return {**DEFAULT_WAKE, "missing": Path(w["model"]).name}
        return w

    def voice(self) -> dict:
        """The effective voice choice, recovered to defaults when the stored one is not supported."""
        from .skills.settings import LIVE_VOICES
        d, v = self._read(), self.cfg.voice
        mode = d.get("voice_mode") if d.get("voice_mode") in ("live", "realtime") else v.mode
        live = (self.memory.get_setting("override:voice.voice") if self.memory is not None else None) or v.voice
        rt = d.get("realtime_voice") or v.realtime_voice
        notes = []
        if live not in LIVE_VOICES:
            notes.append(f"stored GPT-Live voice {live!r} is not supported; using vesper")
            live = "vesper"
        if rt not in REALTIME_VOICES:
            notes.append(f"stored realtime voice {rt!r} is not supported; using cedar")
            rt = "cedar"
        for n in notes:
            logs.warn("identity", "VOICE_RECOVERED", "voice setting recovered", detail=n)
        return {"mode": mode, "live_voice": live, "realtime_voice": rt, "notes": notes,
                "current": f"{mode}:{live if mode == 'live' else rt}" if mode in ("live", "realtime") else None}

    def view(self) -> dict:
        from .voice.wake_train import list_models
        w = self.wake()
        return {**self.names(), "wake": {**w, "builtin": w["model"] == "hey_jarvis",
                                         "capability": _wake_capability(), "models": list_models(self.home),
                                         "train_command": 'jarvis wake train "<phrase>" --say "<Hebrew spelling>"'},
                "voice": self.voice(), "voices": voice_catalog(self.cfg),
                "rules": {"same_model": "allowed (no cost change)",
                          "other_model": "needs approve_cost from the dashboard; refused by speech"},
                "changed": self._read().get("changed", {})}

    # --- writes
    def set_names(self, display_name: str | None = None, spoken_name: str | None = None, *, by: str = "user") -> dict:
        f = {}
        if display_name is not None:
            f["display_name"] = _clean_name(display_name, "display name")
        if spoken_name is not None:
            f["spoken_name"] = _clean_name(spoken_name, "spoken name")
        if not f:
            raise IdentityError("nothing to change")
        self._change(by, **f)
        logs.info("identity", "IDENTITY_RENAMED", "assistant name changed", by=by, fields=sorted(f))
        self.apply_to_config()
        return self.names()

    def set_wake(self, model: str, phrase: str | None = None, threshold: float | None = None, *,
                 by: str = "user", only_in: Path | None = None) -> dict:
        """only_in: the dashboard may only pick models inside <state_dir>/wake (never an arbitrary path)."""
        if model != "hey_jarvis":
            p = Path(model)
            if only_in is not None and only_in.resolve() not in p.resolve().parents:
                raise IdentityError("pick a model trained into the wake folder (jarvis wake train)")
            if p.suffix not in (".npz", ".tflite") or not p.is_file():
                raise IdentityError("wake model must be 'hey_jarvis' or an existing .npz / .tflite file")
            if p.suffix == ".npz":
                from .voice.wake_train import load_head
                try:
                    head = load_head(p)
                except (OSError, ValueError, KeyError) as e:
                    raise IdentityError("not a model made by `jarvis wake train`") from e
                phrase = phrase or head["phrase"]
                threshold = threshold if threshold is not None else head["meta"].get("threshold")
            model = str(p.resolve())
        if threshold is not None and not 0.05 <= float(threshold) <= 0.99:
            raise IdentityError("threshold must be between 0.05 and 0.99")
        w = {"model": model, "phrase": _clean_name(phrase or ("Hey Jarvis" if model == "hey_jarvis" else
                                                             Path(model).stem.replace("_", " ")), "phrase"),
             "threshold": None if threshold is None else round(float(threshold), 2)}
        self._change(by, wake=w)
        logs.info("identity", "WAKE_PHRASE_SET", "wake phrase changed (applies at the next Core start)", by=by,
                  builtin=model == "hey_jarvis")
        return w

    def choose_voice(self, voice_id: str, *, approve_cost: bool = False, by: str = "user") -> dict:
        """voice_id = "live:<name>" or "realtime:<name>". Returns {"ok", "needs_approval", "reason", ...}."""
        cat = {v["id"]: v for v in voice_catalog(self.cfg)}
        if voice_id not in cat:
            raise IdentityError(f"unknown voice {voice_id!r}")
        want, cur = cat[voice_id], self.voice()
        if want["mode"] != cur["mode"]:
            old = (getattr(self.cfg.pricing, "voice_per_minute", {}) or {}).get(
                self.cfg.voice.live_model if cur["mode"] == "live" else self.cfg.voice.realtime_model)
            reason = (f"switching voice model {cur['mode']} -> {want['mode']} ({want['model']}): about "
                      f"${want['usd_per_minute']}/min instead of ${old}/min, different session behaviour")
            if not approve_cost:
                return {"ok": False, "needs_approval": True, "reason": reason}
            self._change(by, voice_mode=want["mode"])
            logs.info("identity", "VOICE_MODE_APPROVED", "voice model switch approved", by=by, mode=want["mode"])
        if want["mode"] == "live":
            if self.memory is None:
                raise IdentityError("voice store not available")
            self.memory.set_setting("override:voice.voice", want["voice"])
        else:
            self._change(by, realtime_voice=want["voice"])
        logs.info("identity", "VOICE_CHOSEN", "voice chosen", voice=voice_id, by=by)
        return {"ok": True, "needs_approval": False, "voice": self.voice(), "applies": "next conversation"}

    def apply_to_config(self, cfg=None) -> None:
        """Names, wake model and voice choices over the loaded config.toml values."""
        cfg = cfg if cfg is not None else self.cfg
        n = self.names()
        cfg.assistant_name, cfg.assistant_name_spoken = n["display_name"], n["spoken_name"]
        w = self.wake()
        if cfg.voice.wake_word not in (w["model"], "hey_jarvis"):
            # V4-VC-8: identity.json owns the wake model; a different config.toml value used to vanish silently.
            logs.warn("identity", "WAKE_CONFIG_OVERRIDDEN", "voice.wake_word in config.toml is replaced by the wake "
                      "model chosen with `jarvis wake use`", source=Path(str(cfg.voice.wake_word)).name,
                      target=Path(str(w["model"])).name)
        cfg.voice.wake_word = w["model"]
        if w.get("threshold") is not None:
            if abs(float(cfg.voice.wake_threshold) - float(w["threshold"])) > 1e-9:
                logs.warn("identity", "WAKE_THRESHOLD_OVERRIDDEN", "voice.wake_threshold in config.toml is replaced by "
                          "the threshold saved with the wake model (identity.json); change it with "
                          "`jarvis wake test --apply`", source=str(cfg.voice.wake_threshold), target=str(w["threshold"]))
            cfg.voice.wake_threshold = w["threshold"]
        cfg.voice.wake_phrase = w["phrase"]
        d = self._read()
        if d.get("voice_mode") in ("live", "realtime") and cfg.voice.mode in ("live", "realtime"):
            cfg.voice.mode = d["voice_mode"]
        if d.get("realtime_voice") in REALTIME_VOICES:
            cfg.voice.realtime_voice = d["realtime_voice"]


# ---------------------------------------------------------------------- the voice Skill
def make_skill(identity: Identity, on_change=None):
    """`assistant_rename` (always confirmed with a read-back) and `voice_catalog` (read)."""
    from pydantic import BaseModel, Field

    from .skills import Skill, Tool, ToolResult

    class RenameArgs(BaseModel):
        display_name: str | None = Field(None, max_length=40, description='written name, e.g. Max')
        spoken_name: str | None = Field(None, max_length=40, description='Hebrew spoken form, e.g. מקס')

    class IdentitySkill(Skill):
        name = "identity"
        description = "My own name (written and spoken), wake phrase and the voices I can use"

        def _wake_line(self) -> str:
            w = identity.wake()
            return f"מילת ההפעלה נשארת \"{w['phrase']}\" עד שמאמנים מילה חדשה בלוח הבקרה."

        def tools(self) -> list[Tool]:
            async def impact(a: RenameArgs) -> tuple[int | None, str]:
                parts = [f"שם כתוב: {a.display_name}" if a.display_name else "",
                         f"שם מדובר: {a.spoken_name}" if a.spoken_name else ""]
                return None, "לשנות את השם שלי? " + ", ".join(p for p in parts if p) + ". " + self._wake_line()

            async def rename(a: RenameArgs) -> ToolResult:
                try:
                    n = identity.set_names(a.display_name, a.spoken_name, by="voice")
                except IdentityError as e:
                    return ToolResult(False, str(e), code="IDENTITY_BAD_NAME")
                if on_change is not None:
                    on_change(n)
                return ToolResult(True, f"מעכשיו קוראים לי {n['spoken_name']}. " + self._wake_line(),
                                  {**n, "wake_phrase": identity.wake()["phrase"], "applies": "next conversation"})

            async def catalog(_) -> ToolResult:
                v = identity.voice()
                same = [x["voice"] for x in voice_catalog(identity.cfg) if x["mode"] == v["mode"]]
                return ToolResult(True, f"הקול שלי עכשיו {v['current']}. אפשר להחליף ל: {', '.join(same[:8])}...",
                                  {"current": v, "same_model_voices": same, "preview": PREVIEW_NOTE,
                                   "rule": "Use set_voice for a voice of the same model. Switching to another voice "
                                           "model changes cost: tell the user to approve it on the dashboard."})
            return [
                Tool("assistant_rename", "Change my name: 'your name is Max', 'קוראים לך מקס'. Give written + Hebrew spoken form; user approves a read-back. Wake phrase unchanged.", rename, RenameArgs, permission="destructive", impact=impact,
                     timeout_s=30),
                Tool("voice_catalog", "Voices I can use, current one, cost rule: 'which voices', 'אילו קולות יש'.", catalog,
                     permission="read"),
            ]
    return IdentitySkill()


# ---------------------------------------------------------------------- dashboard
def _ctx_identity(ctx) -> Identity:
    return Identity(ctx.home, ctx.cfg, ctx.memory)


def dashboard_routes(register) -> None:
    def get(ctx, _m, _b):
        return 200, _ctx_identity(ctx).view()

    def names(ctx, _m, body):
        b = body or {}
        try:
            return 200, _ctx_identity(ctx).set_names(b.get("display_name"), b.get("spoken_name"), by="dashboard")
        except IdentityError as e:
            return 400, {"error": str(e)}

    def voice(ctx, _m, body):
        b = body or {}
        try:
            r = _ctx_identity(ctx).choose_voice(str(b.get("voice", "")), approve_cost=b.get("approve_cost") is True,
                                                by="dashboard")
        except IdentityError as e:
            return 400, {"error": str(e)}
        return (200, r) if r["ok"] else (409, {**r, "error": r["reason"]})

    def wake(ctx, _m, body):
        b = body or {}
        try:
            return 200, {"wake": _ctx_identity(ctx).set_wake(str(b.get("model", "")), b.get("phrase"),
                                                             b.get("threshold"), by="dashboard",
                                                             only_in=ctx.home / "wake"),
                         "applies": "next Core start"}
        except IdentityError as e:
            return 400, {"error": str(e)}
    register("GET", r"/api/identity", get)
    register("POST", r"/api/identity/names", names)
    register("POST", r"/api/identity/voice", voice)
    register("POST", r"/api/identity/wake", wake)
