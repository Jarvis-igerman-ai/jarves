"""SW-V4 F: the bridge store encryption (AES-256-GCM + DPAPI-wrapped key) and the privacy rules of package B.

* Session store round trip, tamper detection, a copied store that does not open with another key (WA_STORE_LOCKED),
  the DPAPI round trip and wrong-entropy refusal (Windows only).
* Planted data: a message body, a contact name, a phone number, the bridge user/admin tokens and a signed approval id
  go through the read, search, send and kill paths with privacy ON and OFF. Then every file JARVES wrote outside the
  bridge's own directory (logs, health, approvals audit, the Core's WhatsApp files) and a `jarvis diag` bundle are
  searched in every encoding of tools/redaction_audit (raw, JSON, URL, base64, UTF-16, NFC/NFD): 0 hits.
* Backups never include the bridge directory."""
from __future__ import annotations

import asyncio
import sys

import pytest

from jarves import approvals, logs
from jarves import whatsapp_policy as pol
from jarves.skills import SkillRegistry
from jarves.whatsapp_bridge import WhatsAppBridgeSkill
from jarves.whatsapp_store import SecureStore, StoreLocked, protect, seal, unprotect, unseal
from tools import redaction_audit as ra
from whatsapp_fixtures import bridge, clock, home, set_send_mode  # noqa: F401

PLANT_JID = "972547654321@s.whatsapp.net"
PLANT_NAME = "Rivka Plantedson"
PLANT_BODY = "the vault code is PLANTEDB0DY and my diagnosis is PLANTEDMEDICAL"
PLANT_HE = "הקוד של הכספת הוא סודיפלנטד"


def test_seal_round_trip_and_tamper():
    key = b"k" * 32
    blob = seal(b"device keys + messages", key)
    assert blob[:4] == b"JWS1" and b"device keys" not in blob
    assert unseal(blob, key) == b"device keys + messages"
    bad = bytearray(blob)
    bad[-1] ^= 1
    with pytest.raises(StoreLocked):
        unseal(bytes(bad), key)
    with pytest.raises(StoreLocked):
        unseal(blob, b"o" * 32)


def test_secure_store_copied_to_another_key_is_locked(tmp_path):
    a = SecureStore(tmp_path / "a", use_dpapi=False)
    a.save("סשן".encode())
    assert a.load() == "סשן".encode() and (tmp_path / "a" / "store.enc").exists()
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    (b_dir / "store.enc").write_bytes((tmp_path / "a" / "store.enc").read_bytes())    # store copied, key not
    with pytest.raises(StoreLocked):
        SecureStore(b_dir, use_dpapi=False).load()
    (b_dir / "store.key").write_bytes(b"x" * 32)                                    # another PC's key
    with pytest.raises(StoreLocked):
        SecureStore(b_dir, use_dpapi=False).load()


def test_fake_bridge_reports_a_locked_store(home, clock):
    from tools.fake_whatsapp_bridge import FakeBridge
    d = home / "whatsapp_bridge"
    b = FakeBridge(d, clock=clock, use_dpapi=False)
    b.add_chat(PLANT_JID, PLANT_NAME)
    (d / "store.key").write_bytes(b"z" * 32)                                        # as if copied to another user
    b2 = FakeBridge(d, clock=clock, use_dpapi=False)
    assert b2.locked and b2.status()["store_locked"] and b2.chats == {}


@pytest.mark.windows_only
def test_dpapi_round_trip_and_wrong_entropy(tmp_path):
    blob = protect(b"\x01" * 32)
    assert blob != b"\x01" * 32 and unprotect(blob) == b"\x01" * 32
    with pytest.raises(StoreLocked):
        unprotect(blob, b"other-entropy")
    s = SecureStore(tmp_path, use_dpapi=True)
    s.save(b"payload")
    raw_key = (tmp_path / "store.key").read_bytes()
    assert len(raw_key) > 32 and SecureStore(tmp_path, use_dpapi=True).load() == b"payload"
    assert s.key_protection == "dpapi"


def test_backups_exclude_the_bridge_dir(bridge, home):
    from jarves import backup
    take, _ = backup.plan(home)
    assert not [n for _, n in take if "whatsapp_bridge" in n]


async def _drive(bridge, home, clock):
    bridge.add_chat(PLANT_JID, PLANT_NAME)
    bridge.deliver(PLANT_JID, PLANT_BODY)
    bridge.deliver(PLANT_JID, PLANT_HE)
    set_send_mode(bridge, PLANT_JID)
    svc = approvals.ApprovalService(home, timeout_s=10, rate_limit=(100, 60.0), clock=clock)

    class Yes:
        name = "cli"

        def available(self, a):
            return True

        def open(self, a):
            svc.answer(a.id, True, channel="cli")

        def close(self, a):
            pass
    svc.channels.append(Yes())
    approvals.attach(svc)
    try:
        reg = SkillRegistry()

        async def confirm(prompt, **meta):
            return await svc.ask(prompt, **meta)
        confirm.accepts_meta = True
        reg.confirm = confirm
        reg.register(WhatsAppBridgeSkill(home, clock=clock))
        reg.allowed_actions |= {"whatsapp_compose"}
        for name, args in (("whatsapp_inbox", {}), ("whatsapp_chat", {"who": PLANT_NAME}),
                           ("whatsapp_search", {"query": "PLANTEDB0DY"}),
                           ("whatsapp_summarize_asked", {"who": [PLANT_NAME]})):
            assert (await reg.call(name, args)).ok, name
            reg._untrusted.clear()
        r = await reg.call("whatsapp_compose", {"who": PLANT_NAME, "text": PLANT_BODY})
        assert (await reg.call("whatsapp_send", {"draft_id": r.data["draft_id"]})).ok
        bad = await reg.call("whatsapp_chat", {"who": "+972 54-765-4321 extra"})        # a failing call too
        assert not bad.ok
    finally:
        approvals.detach(svc)
    from jarves import whatsapp_admin
    whatsapp_admin.kill(home)
    whatsapp_admin.release(home)


@pytest.mark.parametrize("private", [True, False])
def test_no_planted_text_names_numbers_or_tokens_outside_the_bridge(bridge, home, clock, private, tmp_path):
    logs.setup(home / "logs", "VERBOSE", console=False, privacy=private)
    try:
        asyncio.run(_drive(bridge, home, clock))
        secrets_ = [bridge.token, bridge.admin_token,
                    pol.sign_approval(bridge.key, PLANT_JID, PLANT_BODY, exp=clock() + 60)]
        logs.info("whatsapp", "WA_TEST_PLANT", "planted secrets in fields", token=secrets_[0],
                  approval_id=secrets_[2], admin_token=secrets_[1])
        logs.warn("whatsapp", "WA_TEST_PLANT2", "planted in free text", detail=f"Bearer {secrets_[0]} {secrets_[2]}")
    finally:
        logging_shutdown()
    needles = {"secret:bridge_token": [secrets_[0]], "secret:admin_token": [secrets_[1]],
               "secret:approval_id": [secrets_[2].split(".")[2]], "content:body": ["PLANTEDB0DY", "PLANTEDMEDICAL"],
               "content:he": ["סודיפלנטד"], "pii:name": ["Plantedson"], "pii:number": ["7654321"]}
    forms = {k: [(enc, b) for n in ns for enc, b in ra.forms(n).items()] for k, ns in needles.items()}
    hits = {}
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        if not p.is_file() or rel.startswith("whatsapp_bridge/"):
            continue                                       # the bridge's own (encrypted / private) files
        data = p.read_bytes()
        for key, fs in forms.items():
            if key.startswith("pii:number") and rel.startswith("approvals/"):
                pass
            for enc, b in fs:
                if b in data:
                    hits.setdefault(rel, []).append((key, enc))
                    break
    assert not hits, hits
    from jarves import diagbundle
    from jarves.config import Config
    out = tmp_path / f"diag-{private}.zip"
    diagbundle.build(home, Config(), out)
    found = ra.scan_blob("diag", out.read_bytes())
    bundle_hits = {m: [h for h in hs if h[0].startswith(("secret", "content", "pii"))] for m, hs in found.items()}
    assert not any(bundle_hits.values()), bundle_hits
    import zipfile
    with zipfile.ZipFile(out) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    for key, fs in forms.items():
        assert not any(b in blob for _, b in fs), key


def logging_shutdown():
    import logging
    for h in list(logging.getLogger().handlers):
        try:
            h.flush()
        except Exception:
            pass
    sys.stdout.flush()
