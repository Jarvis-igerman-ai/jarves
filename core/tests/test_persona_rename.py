"""R-027 / JAR-196: the owner renames the assistant through the identity layer (the dashboard and setup path,
Identity.set_names) and the NEXT chat turn of the running Core uses the new name; undo brings the old name back.
"JARVES" stays in the prompt as the product name, so the persona check reads the "You are <name>," line."""
from jarves.identity import IdentityError


def _persona(core) -> tuple[str, str]:
    return core.agent.instructions(), core.hello_text(question=True)


async def test_rename_reaches_the_next_chat_turn_and_undo_restores(core):
    prompt, hello = _persona(core)
    assert "You are JARVES," in prompt and "ג'ארוויס" in hello

    core.identity.set_names("Max", "מקס", by="dashboard")
    prompt, hello = _persona(core)
    assert "You are Max," in prompt and "You are JARVES," not in prompt
    assert hello.startswith("שלום! אני מקס.") and "ג'ארוויס" not in hello
    assert core.identity.names() == {"display_name": "Max", "spoken_name": "מקס"}

    core.identity.set_names("JARVES", "ג'ארוויס", by="dashboard")     # undo: back to the previous names
    prompt, hello = _persona(core)
    assert "You are JARVES," in prompt and "You are Max," not in prompt
    assert hello.startswith("שלום! אני ג'ארוויס.") and "מקס" not in hello


async def test_bad_name_changes_nothing(core):
    try:
        core.identity.set_names("<script>", by="dashboard")
    except IdentityError:
        pass
    else:
        raise AssertionError("an invalid name was accepted")
    assert "You are JARVES," in core.agent.instructions()


def test_rename_listeners_get_the_new_names_and_a_broken_one_is_logged(home, caplog):
    from jarves.config import Config
    from jarves.identity import Identity
    ident, seen = Identity(home, Config()), []

    def broken(_n):
        raise RuntimeError("boom")
    ident.on_rename += [broken, seen.append]
    assert ident.set_names("Max", "מקס") == {"display_name": "Max", "spoken_name": "מקס"}
    assert seen == [{"display_name": "Max", "spoken_name": "מקס"}]          # the broken listener did not stop the next
    assert ident.names()["display_name"] == "Max"                            # nor undo the saved rename
    assert any("kept the old name" in r.getMessage() for r in caplog.records)
    ident.set_names("JARVES", "ג'ארוויס")                                     # undo reaches listeners too
    assert seen[-1] == {"display_name": "JARVES", "spoken_name": "ג'ארוויס"}
