"""Fab Browser: an editor tab where an agent looks at fab.com, Epic's asset store, the way a
person would. It searches, opens listings, scrolls and reads the pages from screenshots.
Each result gets a numbered badge, so the agent can open one by its number.

The agent never clicks freely. Unreal editors on one machine can share a single web login
(they do on macOS), so this tab may be signed in to a person's Epic account, and a free
click could buy, add to a library or download. Pages other than searches, listings and
sellers are sent back. People add what an agent picks through the Fab tab in their own
editor (see fab_requests).

Screenshots come from Slate Inspector, which captures the tab's window from outside.
"""

import json
import re
from urllib.parse import urlencode, urlparse

import unreal

import toolset_registry
from copresence import fab_skill, ui_widgets
from copresence.ui_widgets import add

PANEL_ASSET = "/CoPresence/EUW_FabBrowser"
FAB_ORIGIN = "https://www.fab.com"
FAB_HOSTS = ("www.fab.com", "fab.com")
LISTING_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Pages the agent may look at: searches, listings and sellers' pages, in any language
ALLOWED_PATH = re.compile(rf"^(/[a-z]{{2}}(-[a-z]{{2}})?)?/(search(/[\w-]+)*|listings/{LISTING_ID.pattern}|sellers/[^/]+)/?$")
EMPTY_SEARCH = FAB_ORIGIN + "/search?is_free=1&is_ai_forbidden=0"
# Shrinks the page so a screenshot holds more results; the text stays readable
PAGE_ZOOM = 0.6
SCROLL_FRACTION = 0.85

# Numbers every listing card on the page with a yellow badge and remembers the listing
# behind each number. The page can't hand data back to Python on every platform, so
# opening a result by number happens in the page too.
MARK_RESULTS_SCRIPT = """
(function () {
  document.documentElement.style.zoom = '%(zoom)s';
  for (const old of document.querySelectorAll('[data-copresence-badge]')) { old.remove(); }
  const cards = new Map();
  for (const anchor of document.querySelectorAll('a[href*="/listings/"]')) {
    const path = new URL(anchor.getAttribute('href'), location.href).pathname;
    if (!/^\\/listings\\/[0-9a-f-]{36}$/.test(path) || path === location.pathname || cards.has(path)) { continue; }
    cards.set(path, anchor);
  }
  const results = [];
  for (const [path, anchor] of cards) {
    // The link can be a thin overlay, so the badge goes on the card holding the image
    let card = anchor;
    while (card && card !== document.body && !card.querySelector('img')) { card = card.parentElement; }
    if (!card || card === document.body) { card = anchor.parentElement; }
    results.push(location.origin + path);
    if (getComputedStyle(card).position === 'static') { card.style.position = 'relative'; }
    const badge = document.createElement('div');
    badge.textContent = String(results.length);
    badge.setAttribute('data-copresence-badge', '');
    badge.style.cssText = 'position:absolute;left:10px;top:10px;z-index:2147483647;background:#ffd400;color:#000;' +
      'font:800 30px/44px sans-serif;min-width:44px;height:44px;border-radius:22px;text-align:center;' +
      'padding:0 10px;box-shadow:0 0 0 3px #000;pointer-events:none';
    card.appendChild(badge);
  }
  window.__copresenceFabResults = results;
})();
"""
OPEN_RESULT_SCRIPT = """
(function (number) {
  const results = window.__copresenceFabResults || [];
  if (number >= 1 && number <= results.length) { location.assign(results[number - 1]); }
})(%(number)d);
"""
SCROLL_SCRIPT = "window.scrollBy(0, Math.round(innerHeight * %(fraction)f));"
BACK_SCRIPT = "history.back();"
# Stops sending the page back if even the last allowed page keeps leaving
MAX_BOUNCES = 3

_state = {"url": "", "bounces": 0, "loaded_url": "", "loads_reported": False}


def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in FAB_HOSTS and bool(ALLOWED_PATH.match(parsed.path))


def listing_id_in(text: str) -> str | None:
    """The listing id in a Fab listing link, or a bare listing id."""
    match = LISTING_ID.search(text.lower())
    return match.group(0) if match else None


def search_url(query: str, free_only: bool) -> str:
    # Asks Fab to leave out listings whose publishers forbid use with AI, since what the
    # agent sees goes to its model
    parameters = {"q": query.strip(), "is_ai_forbidden": "0"}
    if free_only:
        parameters["is_free"] = "1"
    return f"{FAB_ORIGIN}/search?{urlencode(parameters)}"


def _browser_class():
    # The CoPresence Fab plugin's browser leaves the project name, which says "agent", out
    # of its user agent, so Cloudflare's check on fab.com lets it through. The stock one
    # works until that check comes up.
    return getattr(unreal, "CoPresenceFabBrowser", None) or unreal.WebBrowser


class _FabBrowserPanel:
    """The tab's content: one web browser filling the tab."""

    def __init__(self, root: unreal.VerticalBox):
        ui = ui_widgets.Widgets(root.get_outer())
        root.clear_children()
        self.browser = ui.make(_browser_class())
        # The browser only exists once the tab draws it, so its first page is set up front
        self.browser.set_editor_property("initial_url", _state["url"] or EMPTY_SEARCH)
        self.browser.on_url_changed.add_callable(_on_url_changed)
        if hasattr(self.browser, "on_load_completed"):
            self.browser.on_load_completed.add_callable(self._on_load_completed)
        add(root, self.browser, fill=True)

    def _on_load_completed(self) -> None:
        # Not every platform's browser reports loads (macOS's doesn't), so they only count once one arrives
        _state.update(loaded_url=self.browser.get_url(), loads_reported=True)


tab = ui_widgets.EditorTab(PANEL_ASSET, _FabBrowserPanel)


def _on_url_changed(text) -> None:
    url = str(text)
    if not url.startswith("http"):
        return
    if is_allowed(url):
        _state.update(url=url, bounces=0)
        return
    # A redirect or link left the pages the agent may see; go back to the last one, or to
    # an empty search if that one keeps leaving too
    panel = tab.current()
    _state["bounces"] += 1
    if panel is None or _state["bounces"] > MAX_BOUNCES + 1:
        return
    unreal.log_warning(f"CoPresence Fab Browser: left {url}, going back")
    fallback = _state["url"] if _state["bounces"] <= MAX_BOUNCES else EMPTY_SEARCH
    panel.browser.load_url(fallback or EMPTY_SEARCH)


def _browser() -> unreal.WebBrowser:
    panel = tab.current()
    if panel is None:
        raise RuntimeError("The Fab Browser tab isn't open; search or open a listing first")
    return panel.browser


def go(url: str) -> None:
    if not is_allowed(url):
        raise ValueError(f"The Fab Browser only shows Fab searches, listings and sellers, not {url}")
    _state.update(url=url, loaded_url="")
    panel = tab.current()
    if panel is None:
        # Opening the tab builds the browser on this page
        tab.open()
        return
    # Windows can retain a valid utility widget and tab ID after the tab has
    # disappeared from Slate. Merely spawning/raising it leaves that stale state.
    # An explicit search/listing navigation recreates only our browser tab; the
    # requested URL is already in _state for the replacement browser to load.
    subsystem = unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
    blueprint = unreal.load_asset(PANEL_ASSET)
    tab_id = subsystem.register_tab_and_get_id(blueprint)
    if subsystem.does_tab_exist(tab_id):
        subsystem.close_tab_by_id(tab_id)
    tab.reset()
    tab.open()


def page() -> dict:
    """The page's address and title, and whether it has finished loading; None when the
    browser can't tell."""
    panel = tab.current()
    if panel is None:
        return {"open": False, "url": "", "title": "", "loaded": None}
    url = panel.browser.get_url()
    loaded = (_state["loaded_url"] == url) if _state["loads_reported"] else None
    return {"open": True, "url": url, "title": str(panel.browser.get_title_text()), "loaded": loaded}


@unreal.uclass()
class FabBrowserTools(unreal.ToolsetDefinition):
    """Looking at fab.com, Epic's asset store, in the Fab Browser editor tab: search, open
    listings, scroll and go back, then take a screenshot of the tab's window with Slate
    Inspector. These tools never buy, add to a library or download; people add Fab content
    to the project. The guide tool explains how."""

    @toolset_registry.tool_call
    @staticmethod
    def search(query: str, free_only: bool) -> str:
        """Shows Fab search results in the Fab Browser tab, opening the tab if needed.

        Args:
            query: What to look for, e.g. "low poly lounge sofa".
            free_only: Only show free listings.

        Returns:
            JSON with the search page's address.
        """
        url = search_url(query, free_only)
        go(url)
        return json.dumps({"url": url})

    @toolset_registry.tool_call
    @staticmethod
    def open_listing(listing: str) -> str:
        """Shows a Fab listing in the Fab Browser tab.

        Args:
            listing: A Fab listing link or listing id.

        Returns:
            JSON with the listing page's address.
        """
        listing_id = listing_id_in(listing)
        if listing_id is None:
            raise ValueError(f"No Fab listing id in {listing!r}")
        url = f"{FAB_ORIGIN}/listings/{listing_id}"
        go(url)
        return json.dumps({"url": url})

    @toolset_registry.tool_call
    @staticmethod
    def open_result(number: int) -> str:
        """Opens the listing behind a numbered badge on the current page. Number the
        page with mark_results first.

        Args:
            number: The number on the result's badge.

        Returns:
            JSON with the page's address before opening, to compare against afterwards.
        """
        browser = _browser()
        before = browser.get_url()
        browser.execute_javascript(OPEN_RESULT_SCRIPT % {"number": number})
        return json.dumps({"previous_url": before})

    @toolset_registry.tool_call
    @staticmethod
    def mark_results() -> str:
        """Zooms the page out and numbers its listing cards with yellow badges. Run it
        once a page has loaded, and again after scrolling loads more results.

        Returns:
            JSON with the page's address.
        """
        browser = _browser()
        browser.execute_javascript(MARK_RESULTS_SCRIPT % {"zoom": PAGE_ZOOM})
        return json.dumps({"url": browser.get_url()})

    @toolset_registry.tool_call
    @staticmethod
    def scroll(pages: float) -> str:
        """Scrolls the page by about a screenful per page; negative scrolls up.

        Args:
            pages: How far to scroll, e.g. 1 for one screen down, -1 for one up.

        Returns:
            JSON with the page's address.
        """
        browser = _browser()
        browser.execute_javascript(SCROLL_SCRIPT % {"fraction": pages * SCROLL_FRACTION})
        return json.dumps({"url": browser.get_url()})

    @toolset_registry.tool_call
    @staticmethod
    def back() -> str:
        """Goes back to the previous page.

        Returns:
            JSON with the page's address before going back.
        """
        browser = _browser()
        before = browser.get_url()
        browser.execute_javascript(BACK_SCRIPT)
        return json.dumps({"previous_url": before})

    @toolset_registry.tool_call
    @staticmethod
    def current_page() -> str:
        """What the Fab Browser tab shows.

        Returns:
            JSON with whether the tab is open, and its page's address and title.
        """
        return json.dumps(page())

    @toolset_registry.tool_call
    @staticmethod
    def guide() -> str:
        """How agents work with Fab content in this project. Read it before searching.

        Returns:
            The guide's text.
        """
        return fab_skill.INSTRUCTIONS

    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        from copresence import fab_approvals, fab_requests, hub_client
        return json.dumps({"page": page(), "native_helper": fab_approvals.is_available(),
                           "project": unreal.Paths.get_project_file_path(),
                           "operator": hub_client.display_name(),
                           "approval_ready": hub_client.snapshot()[3] and fab_approvals._state["last_message"] is not None,
                           "imported": fab_requests.imported_listings(),
                           "messages": [message for message in hub_client.snapshot()[1]
                                        if message.get("author") == "QM" or message.get("to") == "QM"][-20:],
                           "approval": ({key: _value for key, _value in fab_approvals._state["active"].items()
                                         if key in ("listing", "stage", "info")}
                                        if fab_approvals._state["active"] else None)})
