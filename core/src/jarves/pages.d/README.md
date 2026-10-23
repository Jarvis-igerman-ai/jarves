# pages.d: how a dashboard page is registered

Add `pages.d/<page>.toml` (see `jarves/page_registry.py`) instead of editing the navigation and the script list in
`dashboard_static/index.html`:

    page = "mypage"
    group = "system"            # top, everyday, automate, activity, actions, assistant, connect, health, usage, dev, system
    he = "העמוד שלי"
    en = "My page"
    scripts = ["/static/mypage.js"]
    styles = ["/static/mypage.css"]

The server fills the markers in index.html when it serves the page. Tests that look for a nav link or script of a NEW
page read `page_registry.compose(index_html)`; pages already written into index.html stay there.
