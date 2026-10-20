"""Generic, config-driven Home Assistant Skill (V3 P1, lowest priority; owner: "generic, config-driven, with a
`jarvis home start` wizard like `jarvis google start`").

- Config: <state_dir>/homeassistant/config.json = {"url": "http://homeassistant.local:8123", "entities": {entity_id:
  {"name": "...", "risky": bool}}}. The long-lived access token is a secret: secrets.env (HOME_ASSISTANT_TOKEN),
  never in the config, logs or errors.
- ALLOW-LIST: JARVES can see and control ONLY the entities in `entities` (the owner picks them in the wizard). Anything
  else is refused (HOME_NOT_ALLOWED) before any request is made. Only a fixed table of simple services per domain
  exists (SERVICES): no arbitrary service calls, no scripts unless the owner listed the script entity.
- RISKY entities (domains lock, cover, alarm_control_panel, script, valve, siren, water_heater by default, or
  flagged by the owner) can only be controlled by `home_control_risky`: a destructive tool whose impact is None, so
  the screen/voice confirmation with the exact action is asked from EVERY origin. `home_control` refuses them.
- REST API only (https://developers.home-assistant.io/docs/api/rest/ : GET /api/, GET /api/states,
  POST /api/services/<domain>/<service>; Bearer token; checked 2026-10-20). Tests run a local fake HA server.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from . import features as feat
from . import clilang
from . import logs
from .channels_common import read_json, read_secret, targets, write_private_json
from .config import save_secret
from .skills import NoArgs, Skill, Tool, ToolResult

TOKEN_ENV = "HOME_ASSISTANT_TOKEN"
START_COMMAND = "jarvis home start"
DEFAULT_URL = "http://homeassistant.local:8123"
RISKY_DOMAINS = frozenset({"lock", "cover", "alarm_control_panel", "script", "valve", "siren", "water_heater"})
# domain -> allowed simple services (the model picks one of these words; nothing else is ever sent)
SERVICES: dict[str, dict[str, str]] = {
    "light": {"on": "turn_on", "off": "turn_off", "toggle": "toggle"},
    "switch": {"on": "turn_on", "off": "turn_off", "toggle": "toggle"},
    "fan": {"on": "turn_on", "off": "turn_off", "toggle": "toggle"},
    "input_boolean": {"on": "turn_on", "off": "turn_off", "toggle": "toggle"},
    "media_player": {"on": "turn_on", "off": "turn_off", "pause": "media_play_pause"},
    "climate": {"on": "turn_on", "off": "turn_off", "set": "set_temperature"},
    "scene": {"on": "turn_on"},
    "script": {"on": "turn_on"},
    "vacuum": {"start": "start", "dock": "return_to_base"},
    "lock": {"lock": "lock", "unlock": "unlock"},
    "cover": {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"},
    "valve": {"open": "open_valve", "close": "close_valve"},
    "water_heater": {"on": "turn_on", "off": "turn_off"},
}
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def config_file(home: Path) -> Path:
    return Path(home) / "homeassistant" / "config.json"


def load_config(home: Path) -> dict:
    d = read_json(config_file(home))
    ents = {k: v for k, v in (d.get("entities") or {}).items() if isinstance(k, str) and _ENTITY.match(k)
            and isinstance(v, dict)}
    return {"url": str(d.get("url", "")).rstrip("/"), "entities": ents}


def is_risky(entity_id: str, meta: dict) -> bool:
    return bool(meta.get("risky")) or entity_id.split(".", 1)[0] in RISKY_DOMAINS


def configured(home: Path | None) -> bool:
    if home is None:
        return False
    c = load_config(home)
    return bool(c["url"] and c["entities"] and os.environ.get(TOKEN_ENV, "").strip())


class HAError(Exception):
    def __init__(self, code: str, message: str, he: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.he = code, message, he or message


class HAClient:
    def __init__(self, url: str, token: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 10.0) -> None:
        self.url, self._token, self.transport, self.timeout = url.rstrip("/"), token, transport, timeout

    async def request(self, method: str, path: str, json_body=None):
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                r = await c.request(method, f"{self.url}{path}", json=json_body,
                                    headers={"Authorization": f"Bearer {self._token}"})
        except httpx.HTTPError:
            raise HAError("HOME_UNREACHABLE", "Home Assistant could not be reached",
                          "לא הצלחתי להגיע ל-Home Assistant. הוא דולק ובאותה רשת?") from None
        if r.status_code in (401, 403):
            raise HAError("HOME_BAD_TOKEN", "Home Assistant rejected the token",
                          f"הטוקן של Home Assistant לא תקף. יוצרים חדש ומריצים {START_COMMAND}.")
        if r.status_code == 404:
            raise HAError("HOME_NOT_FOUND", "entity or service not found", "Home Assistant לא מכיר את זה.")
        if r.status_code >= 400:
            raise HAError("HOME_API_ERROR", f"Home Assistant answered {r.status_code}", "Home Assistant החזיר שגיאה.")
        try:
            return r.json()
        except ValueError:
            return None

    async def check(self) -> bool:
        j = await self.request("GET", "/api/")
        return isinstance(j, dict) and "running" in str(j.get("message", "")).lower()

    async def states(self) -> list[dict]:
        j = await self.request("GET", "/api/states")
        return [s for s in j if isinstance(s, dict) and "entity_id" in s] if isinstance(j, list) else []

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        await self.request("POST", f"/api/services/{domain}/{service}", data)


# ---------------------------------------------------------------------- the Skill
class StateArgs(BaseModel):
    entity: str = Field(..., min_length=1, max_length=80, description="an entity id (light.living_room) or its name")


class ControlArgs(BaseModel):
    entity: str = Field(..., min_length=1, max_length=80, description="an entity id or its name from home_list")
    action: str = Field(..., pattern=r"^[a-z]{2,10}$", description="on | off | toggle | pause | set | start | dock | "
                                                                    "lock | unlock | open | close | stop")
    value: float | None = Field(None, ge=0, le=100, description="light: brightness percent; climate set: temperature")


class HomeSkill(Skill):
    name = "home"
    description = "control the smart-home devices the owner allowed in Home Assistant"

    def __init__(self, home: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.home, self.transport = Path(home), transport

    def _client(self) -> HAClient:
        return HAClient(load_config(self.home)["url"], os.environ.get(TOKEN_ENV, "").strip(), self.transport)

    def _resolve(self, ref: str) -> tuple[str, dict] | None:
        ents = load_config(self.home)["entities"]
        r = ref.strip()
        if r in ents:
            return r, ents[r]
        low = r.casefold()
        hits = [(k, v) for k, v in ents.items() if str(v.get("name", "")).casefold() == low]
        return hits[0] if len(hits) == 1 else None

    def _plan(self, a: ControlArgs) -> tuple[str, dict, str, str, dict] | ToolResult:
        res = self._resolve(a.entity)
        if res is None:
            return ToolResult(False, "אני שולט רק במכשירים שהוגדרו. אין לי את \"" + a.entity[:40] + "\" ברשימה.",
                              code="HOME_NOT_ALLOWED")
        eid, meta = res
        domain = eid.split(".", 1)[0]
        service = SERVICES.get(domain, {}).get(a.action)
        if service is None:
            return ToolResult(False, f"הפעולה {a.action} לא נתמכת עבור {meta.get('name') or eid}.",
                              {"supported": sorted(SERVICES.get(domain, {}))}, code="HOME_BAD_ACTION")
        data: dict = {"entity_id": eid}
        if a.value is not None:
            if domain == "light" and service == "turn_on":
                data["brightness_pct"] = int(a.value)
            elif domain == "climate" and service == "set_temperature":
                data["temperature"] = a.value
        if domain == "climate" and service == "set_temperature" and "temperature" not in data:
            return ToolResult(False, "לאיזו טמפרטורה?", code="HOME_NEEDS_VALUE")
        return eid, meta, domain, service, data

    async def health(self) -> tuple[str, str]:
        if not configured(self.home):
            return "disabled", f"not set up ({START_COMMAND})"
        try:
            await self._client().check()
        except HAError as e:
            return "degraded", e.message
        return "ok", f"{len(load_config(self.home)['entities'])} allowed entities"

    def tools(self) -> list[Tool]:
        async def list_(_a) -> ToolResult:
            ents = load_config(self.home)["entities"]
            try:
                live = {s["entity_id"]: s for s in await self._client().states()}
            except HAError as e:
                return ToolResult(False, e.he, code=e.code)
            rows = [{"entity": k, "name": v.get("name") or (live.get(k) or {}).get("attributes", {}).get("friendly_name")
                     or k, "state": (live.get(k) or {}).get("state", "unknown"), "needs_confirmation": is_risky(k, v)}
                    for k, v in ents.items()]
            return ToolResult(True, f"{len(rows)} מכשירים מורשים.", {"devices": rows})

        async def state(a: StateArgs) -> ToolResult:
            res = self._resolve(a.entity)
            if res is None:
                return ToolResult(False, "המכשיר הזה לא ברשימה המורשית.", code="HOME_NOT_ALLOWED")
            eid, meta = res
            try:
                j = await self._client().request("GET", f"/api/states/{eid}")
            except HAError as e:
                return ToolResult(False, e.he, code=e.code)
            st = (j or {}).get("state", "unknown")
            return ToolResult(True, f"{meta.get('name') or eid}: {st}", {"entity": eid, "state": st})

        async def do(a: ControlArgs, risky: bool) -> ToolResult:
            plan = self._plan(a)
            if isinstance(plan, ToolResult):
                return plan
            eid, meta, domain, service, data = plan
            if is_risky(eid, meta) != risky:
                if risky:      # the confirmed tool is only for risky entities
                    return ToolResult(False, "המכשיר הזה לא דורש אישור מיוחד; משתמשים ב-home_control.",
                                      code="HOME_NOT_RISKY")
                return ToolResult(False, "המכשיר הזה דורש אישור שלך. אשתמש ב-home_control_risky ואבקש אישור.",
                                  code="HOME_NEEDS_CONFIRM")
            try:
                await self._client().call_service(domain, service, data)
            except HAError as e:
                return ToolResult(False, e.he, code=e.code)
            logs.info("home", "HOME_CONTROL", "home device action done", domain=domain, service=service, risky=risky)
            return ToolResult(True, f"בוצע: {meta.get('name') or eid} - {a.action}.", {"entity": eid},
                              code="HOME_DONE")

        async def control(a: ControlArgs) -> ToolResult:
            return await do(a, False)

        async def control_risky(a: ControlArgs) -> ToolResult:
            return await do(a, True)

        async def risky_impact(a: ControlArgs) -> tuple[int | None, str]:
            res = self._resolve(a.entity)
            name = (res[1].get("name") or res[0]) if res else a.entity
            return None, f"לבצע ב-Home Assistant: {a.action} על \"{name}\"? / Do \"{a.action}\" on \"{name}\"?"

        return [
            Tool("home_list", "List the smart-home devices the owner allowed (name, state, whether they need confirmation).",
                 list_, NoArgs, permission="read", timeout_s=15),
            Tool("home_state", "Current state of one allowed smart-home device.", state, StateArgs, permission="read",
                 timeout_s=15),
            Tool("home_control", "Switch a normal allowed device (lights, switches, fans, media, climate, scenes). Devices "
                 "that need confirmation (locks, covers, scripts, alarm) are refused here.", control, ControlArgs,
                 permission="action", timeout_s=15),
            Tool("home_control_risky", "Control a device that needs the owner's confirmation (lock, cover/garage, script, "
                 "valve...). The user approves the exact action on the screen.", control_risky, ControlArgs,
                 permission="destructive", timeout_s=20, impact=risky_impact),
        ]


ALLOWED_ACTIONS = frozenset({"home_control"})


def register_home(registry, home: Path, health=None, transport=None) -> HomeSkill:
    skill = HomeSkill(home, transport)
    registry.register(skill)
    registry.allowed_actions |= ALLOWED_ACTIONS
    if health is not None:
        if configured(home):
            health.set("home", "ok", "Home Assistant configured", START_COMMAND)
        else:
            health.set("home", "disabled", "Home Assistant not set up", START_COMMAND)
    return skill


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    if not configured(c.home):
        return False, f"Home Assistant not set up; tell the user: \"תריץ במחשב {START_COMMAND}, ואני אדריך אותך\""
    return True, ""


feat.register("home_assistant", "הבית החכם (Home Assistant)", "Smart home (Home Assistant)", False, probe=_probe,
              needs=["home_assistant_server"], risk="high", skills=["home"],
              about="control only the devices you allowed in Home Assistant; locks, garage doors and scripts always "
                    "ask you first (long-lived token stays on this computer)",
              targets=targets("UNIT test_channels_home.py (local fake Home Assistant server)",
                              "never connected to a real Home Assistant"),
              permissions=["action", "destructive (always confirmed)"], settings=START_COMMAND)


# ---------------------------------------------------------------------- the wizard (`jarvis home start`)
PICKABLE = tuple(SERVICES)


def parse_picks(text: str, n: int) -> list[int]:
    """'1,3,5-7' -> [0,2,4,5,6] (1-based input, validated against n)."""
    out: list[int] = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(part)
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not (1 <= a <= b <= n):
            raise ValueError(part)
        out += [i - 1 for i in range(a, b + 1) if i - 1 not in out]
    return out


class Wizard:
    def __init__(self, home: Path, io, *, transport=None) -> None:
        self.home, self.io, self.transport = Path(home), io, transport

    async def run(self) -> int:
        out = lambda t="": self.io.out(clilang.bi(t))      # noqa: E731
        cur = load_config(self.home)
        out("JARVES + Home Assistant\n" + clilang.fmt(
            "1. ב-Home Assistant: לוחצים על השם שלכם (למטה משמאל) > Security > Long-lived access tokens > Create token.\n"
            "2. מעתיקים את הטוקן (מוצג פעם אחת).",
            "1. In Home Assistant: your profile (bottom left) > Security > Long-lived access tokens > Create token.\n"
            "2. Copy the token (shown once).") + "\n")
        url = (self.io.read(f"Home Assistant address [{cur['url'] or DEFAULT_URL}]: ") or "").strip() \
            or cur["url"] or DEFAULT_URL
        sp = urlsplit(url)
        if sp.scheme not in ("http", "https") or not sp.hostname:
            out("הכתובת חייבת להתחיל ב-http:// או https://. / The address must start with http:// or https://")
            return 1
        url = f"{sp.scheme}://{sp.netloc}"
        tok = (read_secret(self.io, "Long-lived token (hidden, Enter = keep the saved one): ") or "").strip() \
            or os.environ.get(TOKEN_ENV, "").strip()
        if not tok:
            out("בוטל, לא נשמר כלום. / Cancelled, nothing saved.")
            return 1
        client = HAClient(url, tok, self.transport)
        try:
            if not await client.check():
                out("הכתובת עונה אבל זה לא נראה כמו Home Assistant. / That address does not look like Home Assistant.")
                return 1
            states = await client.states()
        except HAError as e:
            out(clilang.fmt(e.he, e.message))
            return 1
        cands = sorted((s for s in states if s["entity_id"].split(".", 1)[0] in PICKABLE),
                       key=lambda s: s["entity_id"])
        if not cands:
            out("לא מצאתי מכשירים שאפשר לשלוט בהם. / No controllable devices found.")
            return 1
        out("\nמכשירים / Devices:")
        for i, s in enumerate(cands, 1):
            eid = s["entity_id"]
            name = (s.get("attributes") or {}).get("friendly_name") or eid
            out(f"  {i:>3}. {name}  [{eid}]" + ("  (ask first)" if eid.split('.')[0] in RISKY_DOMAINS else ""))
        try:
            picks = parse_picks(self.io.read("\nWhich devices may JARVES use? numbers like 1,3,5-7 (Enter = none): ")
                                or "", len(cands))
        except ValueError as e:
            out(f"לא הבנתי: {e}. / Not understood: {e}")
            return 1
        if not picks:
            out("לא נבחר כלום, לא שמרתי. / Nothing chosen, nothing saved.")
            return 1
        chosen = [cands[i] for i in picks]
        out("\nנבחרו / Chosen:")
        for i, s in enumerate(chosen, 1):
            out(f"  {i:>3}. {s['entity_id']}")
        try:
            extra = parse_picks(self.io.read("Which of these must ALWAYS ask you first (besides locks, covers, scripts, "
                                             "alarms which always do)? numbers (Enter = none): ") or "", len(chosen))
        except ValueError as e:
            out(f"לא הבנתי: {e}. / Not understood: {e}")
            return 1
        ents = {}
        for i, s in enumerate(chosen):
            eid = s["entity_id"]
            meta = {"name": str((s.get("attributes") or {}).get("friendly_name") or eid)[:60]}
            if i in extra:
                meta["risky"] = True
            ents[eid] = meta
        write_private_json(config_file(self.home), {"url": url, "entities": ents})
        save_secret(TOKEN_ENV, tok, self.home)
        logs.info("home", "HOME_CONFIGURED", "Home Assistant configured", entities=len(ents))
        out(f"נשמר: {len(ents)} מכשירים. מפעילים:  jarvis features on home_assistant  (כבוי כברירת מחדל). "
            f"/ Saved {len(ents)} devices. Switch it on: jarvis features on home_assistant (off by default).")
        return 0


def add_cli(sub) -> None:
    h = sub.add_parser("home", help="smart home (Home Assistant): `jarvis home start` guides you; only the devices you "
                                    "pick, risky ones ask first")
    h.add_argument("action", choices=["start", "status", "test", "logout"])


def cmd_home(a, cfg, home) -> int:
    import json

    from .setup_wizard import ConsoleIO
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    if a.action == "start":
        return asyncio.run(Wizard(home, ConsoleIO()).run())
    c = load_config(home)
    if a.action == "status":
        print(json.dumps({"url": c["url"], "token_saved": bool(os.environ.get(TOKEN_ENV, "").strip()),
                          "entities": {k: {**v, "risky": is_risky(k, v)} for k, v in c["entities"].items()}},
                         ensure_ascii=False))
        return 0 if configured(home) else 1
    if a.action == "logout":
        config_file(home).unlink(missing_ok=True)
        from .config import secrets_file
        p = secrets_file(home)
        if p.exists():
            keep = [l for l in p.read_text(encoding="utf-8-sig").splitlines() if l.partition("=")[0].strip() != TOKEN_ENV]
            p.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.environ.pop(TOKEN_ENV, None)
        print("Home Assistant disconnected (config and token removed). Also delete the token in your HA profile.")
        return 0

    async def go() -> int:
        try:
            ok = await HAClient(c["url"], os.environ.get(TOKEN_ENV, "").strip()).check()
        except HAError as e:
            print(f"{e.code}: {e.message}")
            return 1
        print("ok" if ok else "answers, but not Home Assistant")
        return 0 if ok else 1
    return asyncio.run(go())
