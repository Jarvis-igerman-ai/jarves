# hebrew_parts: Hebrew for server-supplied texts, one file per change

Add `<YYYY-MM-DD>-<topic>.json` here with `{"English text": "עברית"}` for every fixed English text the API sends that the
dashboard shows in Hebrew (feature descriptions, blockers, notes, labels; see `jarves/hebrew.py`). Do not add lines to
`TEXT` in `hebrew.py`: two PRs adding lines there always conflicted. Regex rules for dynamic texts stay in `RULES`.
A text lives in one place only (`tests/test_hebrew_texts.py` checks it).
