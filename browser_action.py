import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import quote, urlparse

import trafilatura
from loguru import logger

from .browser.manager import get_shared_browser_manager


_executor = ThreadPoolExecutor(max_workers=2)
_tabs: Dict[str, Any] = {}
_active_tab_id: Optional[str] = None
_next_tab_index = 1
_tab_lock = asyncio.Lock()
_last_browser_error = ""


async def browser_action(
    action: str,
    url: str = "",
    query: str = "",
    selector: str = "",
    ref: str = "",
    tab_id: str = "",
    text: str = "",
    key: str = "",
    target: str = "",
    timeout: float = 10.0,
    headless: bool = True,
) -> str:
    """
    Operate persistent browser tabs and return a compact page observation.

    Supported actions:
    - search: create a tab with DuckDuckGo search results
    - new_tab: create a tab, optionally with url
    - navigate: open url
    - click: click selector, or a visible text target
    - type: type text into selector, focused element, or best-effort input
    - press: press key (Enter by default)
    """
    async with _tab_lock:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            _browser_action_sync,
            action,
            url,
            query,
            selector,
            ref,
            tab_id,
            text,
            key,
            target,
            timeout,
            headless,
        )


def _browser_action_sync(
    action: str,
    url: str,
    query: str,
    selector: str,
    ref: str,
    tab_id: str,
    text: str,
    key: str,
    target: str,
    timeout: float,
    headless: bool,
) -> str:
    tab = None
    normalized = (action or "").strip().lower()
    auto_scan: Optional[Dict[str, Any]] = None
    try:
        if normalized in {"go", "open"}:
            normalized = "navigate"
        if normalized in {"input", "fill"}:
            normalized = "type"
        if normalized in {"enter", "return"}:
            normalized = "press"
            key = key or "enter"

        if normalized not in {"search", "new_tab", "navigate", "click", "type", "press"}:
            return _json_error(f"unsupported action: {action}")

        if normalized == "search":
            clean_query = (query or text or target or "").strip()
            if not clean_query:
                return _json_error("search requires query")
            search_url = _build_search_url(clean_query)
            tab_id, tab = _create_tab(headless=headless, url=search_url)
            if tab is None:
                return _json_error("browser not available", detail=_last_browser_error)
            _wait(tab, timeout)
            _maybe_auto_click_simple_verification(tab, timeout=timeout)
            auto_scan = _scan_to_bottom(tab)
            return _observe(tab, action=normalized, tab_id=tab_id, auto_scan=auto_scan)

        if normalized == "new_tab":
            tab_id, tab = _create_tab(headless=headless, url=url)
            if tab is None:
                return _json_error("browser not available", detail=_last_browser_error)
            if url:
                _wait(tab, timeout)
                _maybe_auto_click_simple_verification(tab, timeout=timeout)
                auto_scan = _scan_to_bottom(tab)
            return _observe(tab, action=normalized, tab_id=tab_id, auto_scan=auto_scan)

        tab_id, tab = _get_or_create_tab(tab_id=tab_id, headless=headless, url=url if normalized == "navigate" else "")
        if tab is None:
            return _json_error("browser not available", detail=_last_browser_error)

        if normalized == "navigate":
            if not url:
                return _json_error("navigate requires url")
            tab.get(url)
            _wait(tab, timeout)
            _maybe_auto_click_simple_verification(tab, timeout=timeout)
            auto_scan = _scan_to_bottom(tab)

        elif normalized == "click":
            ele = _find_element(tab, selector=selector, ref=ref, target=target or text, timeout=timeout)
            if ele is None:
                if not (ref and _click_ref_with_js(tab, ref)):
                    return _json_error("click target not found", tab)
            else:
                ele.click()
            _wait(tab, timeout)
            auto_scan = _scan_to_bottom(tab)

        elif normalized == "type":
            if not text:
                return _json_error("type requires text", tab)
            ele = _find_element(tab, selector=selector, ref=ref, target=target, timeout=min(timeout, 3.0))
            if ele is None:
                ele = _find_default_input(tab)
            if ele is not None:
                try:
                    ele.clear()
                except Exception:
                    pass
                ele.input(text)
            else:
                tab.actions.type(text)
            time.sleep(0.2)

        elif normalized == "press":
            press_key = (key or "enter").strip().lower()
            tab.actions.key_down(press_key).key_up(press_key)
            _wait(tab, timeout)
            auto_scan = _scan_to_bottom(tab)

        return _observe(tab, action=normalized, tab_id=tab_id, auto_scan=auto_scan)
    except Exception as exc:
        logger.warning(f"browser_action failed: {type(exc).__name__}: {exc}")
        return _json_error("browser_action failed", tab, detail=f"{type(exc).__name__}: {exc}")


def _get_or_create_tab(tab_id: str, headless: bool, url: str = "") -> Tuple[str, Optional[Any]]:
    tab = _get_tab(tab_id=tab_id, headless=headless)
    if tab is not None:
        return _resolve_tab_id(tab_id), tab
    return _create_tab(headless=headless, url=url)


def _get_tab(tab_id: str, headless: bool) -> Optional[Any]:
    global _active_tab_id
    manager = get_shared_browser_manager(headless=headless)
    resolved_id = _resolve_tab_id(tab_id)
    if not resolved_id:
        return None

    try:
        tab = _tabs.get(resolved_id)
        if tab is not None:
            _ = tab.url
            _set_active_tab(resolved_id)
            return tab
    except Exception:
        _tabs.pop(resolved_id, None)
        if _active_tab_id == resolved_id:
            _active_tab_id = next(iter(_tabs.keys()), None)
    return None


def _create_tab(headless: bool, url: str = "") -> Tuple[str, Optional[Any]]:
    global _next_tab_index, _last_browser_error
    manager = get_shared_browser_manager(headless=headless)
    try:
        tab = manager.new_tab(url or None)
        tab_id = f"tab-{_next_tab_index}"
        _next_tab_index += 1
        _tabs[tab_id] = tab
        _set_active_tab(tab_id)
        return tab_id, tab
    except Exception as exc:
        _last_browser_error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"browser_action: failed to create tab: {_last_browser_error}")
        return "", None


def _resolve_tab_id(tab_id: str) -> str:
    wanted = (tab_id or "").strip()
    if wanted:
        return wanted
    return _active_tab_id or ""


def _set_active_tab(tab_id: str) -> None:
    global _active_tab_id
    if tab_id in _tabs:
        _active_tab_id = tab_id


def close_browser_action_tabs() -> None:
    """Close tabs tracked by browser_action and reset local tab state."""
    global _active_tab_id
    closed = 0
    for tab_id, tab in list(_tabs.items()):
        try:
            tab.close()
            closed += 1
        except Exception as exc:
            logger.debug(f"browser_action: failed to close {tab_id}: {exc}")
    _tabs.clear()
    _active_tab_id = None
    if closed:
        logger.info(f"browser_action: closed {closed} task tab(s); shared browser kept alive")


def _wait(tab: Any, timeout: float) -> None:
    try:
        tab.wait.doc_loaded(timeout=timeout)
    except Exception:
        pass
    time.sleep(0.3)


def _maybe_auto_click_simple_verification(tab: Any, timeout: float = 10.0) -> bool:
    """Click simple Bing-style human verification boxes; never solve puzzle CAPTCHAs."""
    try:
        title = str(tab.title or "")
        url = str(tab.url or "")
        html = str(tab.html or "")
    except Exception:
        return False

    probe = "\n".join([title, url, html[:5000]]).lower()
    if "bing.com" not in probe:
        return False
    if not any(term in probe for term in ["最后一步", "请解决以下难题", "请验证您是真人", "verify you are human"]):
        return False
    if any(term in probe for term in ["选择所有", "请选择", "select all", "拖动", "滑块", "puzzle", "输入答案"]):
        return False

    selectors = [
        "xpath://*[contains(normalize-space(.), '请验证您是真人')]",
        "xpath://*[contains(normalize-space(.), '我不是机器人')]",
        "xpath://*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify you are human')]",
        "xpath://input[@type='checkbox']",
        "xpath://div[@role='checkbox']",
    ]
    for selector in selectors:
        try:
            ele = tab.ele(selector, timeout=1.0)
            if ele:
                ele.click()
                logger.info("browser_action: auto-clicked simple Bing verification control")
                _wait(tab, timeout)
                return True
        except Exception as exc:
            logger.debug(f"browser_action: simple verification click failed for {selector}: {exc}")

    try:
        clicked = tab.run_js(
            """
            (() => {
              const nodes = Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"], label, div, span, button'));
              const target = nodes.find(el => /请验证您是真人|我不是机器人|verify you are human/i.test(el.innerText || el.value || el.getAttribute('aria-label') || ''));
              const clickAt = (x, y) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return false;
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'pointerup', 'click']) {
                  el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y }));
                }
                return true;
              };
              if (target) {
                const rect = target.getBoundingClientRect();
                target.click();
                if (clickAt(Math.max(1, rect.left + 24), rect.top + rect.height / 2)) return true;
                if (clickAt(Math.max(1, rect.left - 28), rect.top + rect.height / 2)) return true;
                return true;
              }
              const textNode = Array.from(document.querySelectorAll('body *')).find(el =>
                /请验证您是真人|我不是机器人|verify you are human/i.test(el.innerText || '')
              );
              if (!textNode) return false;
              const rect = textNode.getBoundingClientRect();
              return clickAt(Math.max(1, rect.left - 32), rect.top + rect.height / 2)
                || clickAt(Math.max(1, rect.left + 24), rect.top + rect.height / 2);
            })()
            """,
            as_expr=True,
        )
        if clicked:
            logger.info("browser_action: auto-clicked simple Bing verification control via JS")
            _wait(tab, timeout)
            return True
    except Exception as exc:
        logger.debug(f"browser_action: simple verification JS click failed: {exc}")
    return False


def _scan_to_bottom(tab: Any) -> Dict[str, Any]:
    last_state: Dict[str, Any] = {}
    for step in range(12):
        state = tab.run_js(
            """
            const doc = document.documentElement;
            const body = document.body || {};
            const scroller = document.scrollingElement || doc || body;
            if (doc) doc.style.scrollBehavior = 'auto';
            if (body && body.style) body.style.scrollBehavior = 'auto';
            const viewport = window.innerHeight || doc.clientHeight || 900;
            const scrollHeight = Math.max(scroller.scrollHeight || 0, doc.scrollHeight || 0, body.scrollHeight || 0);
            const before = Math.round(scroller.scrollTop || window.scrollY || doc.scrollTop || body.scrollTop || 0);
            const next = Math.min(scrollHeight, before + Math.max(viewport * 3, 1800));
            scroller.scrollTop = next;
            if (doc) doc.scrollTop = next;
            if (body) body.scrollTop = next;
            window.scrollTo(0, next);
            const after = Math.round(scroller.scrollTop || window.scrollY || doc.scrollTop || body.scrollTop || 0);
            const atBottom = after + viewport >= scrollHeight - 8;
            return { before, after, viewport, scrollHeight, atBottom };
            """
        )
        if isinstance(state, dict):
            last_state = state
            logger.debug(
                "browser_action: auto-scan step {} before={} after={} height={} at_bottom={}",
                step + 1,
                state.get("before"),
                state.get("after"),
                state.get("scrollHeight"),
                state.get("atBottom"),
            )
        if isinstance(state, dict) and state.get("atBottom"):
            break
        if isinstance(state, dict) and state.get("after") == state.get("before") and step > 0:
            break
        time.sleep(0.12)

    try:
        tab.actions.key_down("end").key_up("end")
    except Exception as exc:
        logger.debug(f"browser_action: auto-scan End key fallback failed: {exc}")
    tab.run_js(
        """
        const doc = document.documentElement;
        const body = document.body || {};
        const scroller = document.scrollingElement || doc || body;
        const bottom = Math.max(scroller.scrollHeight || 0, doc.scrollHeight || 0, body.scrollHeight || 0);
        scroller.scrollTop = bottom;
        if (doc) doc.scrollTop = bottom;
        if (body) body.scrollTop = bottom;
        window.scrollTo(0, bottom);
        """
    )
    final_state = _wait_for_scroll_stable(tab)
    logger.info(
        "browser_action: auto-scan completed scroll_y={} scroll_h={} at_bottom={} remaining_y={}",
        final_state.get("scroll_y"),
        final_state.get("scroll_h"),
        final_state.get("at_bottom"),
        final_state.get("remaining_y"),
    )
    return {
        "enabled": True,
        "last_step": last_state,
        "final_state": final_state,
    }


def _wait_for_scroll_stable(tab: Any, timeout: float = 2.0) -> Dict[str, Any]:
    deadline = time.time() + timeout
    stable_count = 0
    previous: Dict[str, Any] = {}
    current: Dict[str, Any] = {}

    while time.time() < deadline:
        current = _get_page_state(tab)
        if (
            current
            and previous
            and current.get("scroll_y") == previous.get("scroll_y")
            and current.get("scroll_h") == previous.get("scroll_h")
        ):
            stable_count += 1
            if stable_count >= 3:
                return current
        else:
            stable_count = 0
        previous = current
        time.sleep(0.15)

    return current or previous


def _get_page_state(tab: Any) -> Dict[str, Any]:
    try:
        return tab.run_js(
            """
            (() => {
              const doc = document.documentElement;
              const body = document.body || {};
              const scroll_y = Math.round(window.scrollY || doc.scrollTop || body.scrollTop || 0);
              const viewport_h = Math.round(window.innerHeight || doc.clientHeight || 0);
              const scroll_h = Math.round(Math.max(doc.scrollHeight || 0, body.scrollHeight || 0));
              return {
                scroll_y,
                viewport_h,
                scroll_h,
                at_top: scroll_y <= 4,
                at_bottom: scroll_y + viewport_h >= scroll_h - 8,
                remaining_y: Math.max(0, scroll_h - scroll_y - viewport_h)
              };
            })()
            """,
            as_expr=True,
        ) or {}
    except Exception as exc:
        logger.debug(f"browser_action: page_state unavailable: {type(exc).__name__}: {exc}")
        return {}


def _get_visible_text(tab: Any) -> str:
    try:
        return str(tab.run_js(
            """
            (() => {
              const viewportH = window.innerHeight || document.documentElement.clientHeight || 900;
              const nodes = Array.from(document.querySelectorAll('body *'))
                .filter(el => {
                  const rect = el.getBoundingClientRect();
                  if (rect.width <= 0 || rect.height <= 0) return false;
                  if (rect.bottom < 0 || rect.top > viewportH) return false;
                  const style = window.getComputedStyle(el);
                  if (style.visibility === 'hidden' || style.display === 'none') return false;
                  const text = (el.innerText || el.textContent || '').trim();
                  return text && text.length > 1;
                })
                .map(el => (el.innerText || el.textContent || '').trim())
                .filter(Boolean);
              return Array.from(new Set(nodes)).join('\\n').slice(0, 5000);
            })()
            """,
            as_expr=True,
        ) or "")
    except Exception as exc:
        logger.debug(f"browser_action: visible_text unavailable: {type(exc).__name__}: {exc}")
        return ""


def _find_element(tab: Any, selector: str = "", ref: str = "", target: str = "", timeout: float = 5.0) -> Optional[Any]:
    if ref:
        ele = tab.ele(f"[data-hyw-ref='{ref}']", timeout=timeout)
        if ele:
            return ele

    if selector:
        ele = tab.ele(selector, timeout=timeout)
        if ele:
            return ele

    label = (target or "").strip()
    if not label:
        return None

    # DrissionPage supports XPath selectors; prefer exact visible text, then contains.
    xpath_literal = _xpath_literal(label)
    candidates = [
        f"xpath://button[normalize-space(.)={xpath_literal}]",
        f"xpath://a[normalize-space(.)={xpath_literal}]",
        f"xpath://*[self::button or self::a or @role='button'][contains(normalize-space(.), {xpath_literal})]",
        f"xpath://input[@value={xpath_literal} or @aria-label={xpath_literal} or @placeholder={xpath_literal}]",
        f"xpath://textarea[@aria-label={xpath_literal} or @placeholder={xpath_literal}]",
    ]
    for candidate in candidates:
        ele = tab.ele(candidate, timeout=0.8)
        if ele:
            return ele
    return None


def _find_default_input(tab: Any) -> Optional[Any]:
    for selector in [
        "textarea",
        "input[type='search']",
        "input[name='q']",
        "input[type='text']",
        "input:not([type])",
    ]:
        ele = tab.ele(selector, timeout=0.5)
        if ele:
            return ele
    return None


def _observe(tab: Any, action: str, tab_id: str, auto_scan: Optional[Dict[str, Any]] = None) -> str:
    observe_errors: List[str] = []

    try:
        _annotate_interactive_elements(tab)
    except Exception as exc:
        observe_errors.append(f"annotate: {type(exc).__name__}: {exc}")
        time.sleep(0.5)
        try:
            _annotate_interactive_elements(tab)
        except Exception as retry_exc:
            observe_errors.append(f"annotate_retry: {type(retry_exc).__name__}: {retry_exc}")

    try:
        html = tab.html or ""
    except Exception as exc:
        html = ""
        observe_errors.append(f"html: {type(exc).__name__}: {exc}")

    try:
        content = trafilatura.extract(
            html,
            include_links=True,
            include_images=False,
            include_comments=False,
            include_tables=True,
            favor_precision=False,
            output_format="markdown",
        ) or ""
    except Exception as exc:
        content = ""
        observe_errors.append(f"extract: {type(exc).__name__}: {exc}")

    try:
        elements = tab.run_js(
            """
            (() => Array.from(document.querySelectorAll('a,button,input,textarea,select,label,[role="button"],[role="checkbox"],[tabindex],iframe,[aria-label],[title]'))
                .filter(el => {
                  const rect = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                })
                .slice(0, 25)
                .map((el, idx) => {
                  const rect = el.getBoundingClientRect();
                  const aria = el.getAttribute('aria-label') || '';
                  const title = el.getAttribute('title') || '';
                  const name = el.getAttribute('name') || '';
                  let ref = el.getAttribute('data-hyw-ref') || '';
                  if (!ref) {
                    ref = String(idx + 1);
                    el.setAttribute('data-hyw-ref', ref);
                  }
                  return {
                    ref,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    type: el.getAttribute('type') || '',
                    text: (el.innerText || el.value || el.placeholder || aria || title || name || '').trim().slice(0, 80),
                    selector: el.id ? `#${el.id}` : '',
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2)
                  };
                }))()
            """,
            as_expr=True,
        ) or []
    except Exception as exc:
        elements = []
        observe_errors.append(f"elements: {type(exc).__name__}: {exc}")

    try:
        title = tab.title
    except Exception as exc:
        title = ""
        observe_errors.append(f"title: {type(exc).__name__}: {exc}")

    try:
        current_url = tab.url
    except Exception as exc:
        current_url = ""
        observe_errors.append(f"url: {type(exc).__name__}: {exc}")

    page_state = _get_page_state(tab)
    if not page_state:
        observe_errors.append("page_state: unavailable")

    visible_text = _get_visible_text(tab)
    search_results = _extract_search_results(tab, current_url)

    challenge = _detect_challenge(title=title, url=current_url, content=content, elements=elements)

    is_search_page = bool(search_results)
    is_challenge = bool(challenge)

    payload: Dict[str, Any] = {
        "ok": True,
        "action": action,
        "active_tab_id": tab_id,
        "tabs": _list_tabs(),
        "title": title,
        "url": current_url,
        "page_state": {
            "at_bottom": bool(page_state.get("at_bottom")) if isinstance(page_state, dict) else False,
            "remaining_y": page_state.get("remaining_y", 0) if isinstance(page_state, dict) else 0,
        },
    }
    if search_results:
        payload["search_results"] = search_results
    elif content:
        payload["content"] = content[:8000]
        if len(content) > 8000:
            payload["content_truncated"] = True
    elif visible_text:
        payload["visible_text"] = visible_text[:1600]
        if len(visible_text) > 1600:
            payload["visible_text_truncated"] = True

    if is_challenge:
        payload["visible_text"] = visible_text[:1600]
        payload["interactive_elements"] = elements[:12]
    elif not is_search_page and not content and elements:
        payload["interactive_elements"] = elements[:12]

    if auto_scan:
        payload["auto_scan"] = {
            "enabled": auto_scan.get("enabled", True),
            "at_bottom": bool((auto_scan.get("final_state") or {}).get("at_bottom")),
        }
    if challenge:
        payload["page_notice"] = challenge
    if observe_errors:
        payload["observe_warnings"] = observe_errors
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_search_results(tab: Any, current_url: str) -> List[Dict[str, str]]:
    host = (urlparse(current_url or "").netloc or "").lower()
    if not any(domain in host for domain in ("duckduckgo.", "bing.", "google.")):
        return []

    try:
        raw_results = tab.run_js(
            """
            (() => {
              const blockedHosts = ['bing.com', 'google.com', 'duckduckgo.com', 'microsoft.com'];
              const decodeRedirect = (href) => {
                try {
                  const parsed = new URL(href);
                  const encoded = parsed.searchParams.get('u');
                  if (encoded) {
                    const raw = encoded.startsWith('a1') ? encoded.slice(2) : encoded;
                    const padded = raw.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(raw.length / 4) * 4, '=');
                    const decoded = atob(padded);
                    if (/^https?:\\/\\//i.test(decoded)) return decoded;
                  }
                } catch (_) {}
                return href;
              };
              const looksLikeUrl = (text) => {
                const s = (text || '').trim();
                return /^(https?:\\/\\/|www\\.|[\\w.-]+\\.[a-z]{2,})(\\s|\\/|$)/i.test(s) ||
                  /^https?:\\/\\//i.test(s.replace(/\\s+/g, ''));
              };
              const cleanText = (text) => (text || '').replace(/\\s+/g, ' ').trim();
              const usefulLines = (text, title) => {
                const titleText = cleanText(title).toLowerCase();
                return (text || '')
                  .split(/\\n+/)
                  .map(cleanText)
                  .filter(Boolean)
                  .filter(line => line.length > 20)
                  .filter(line => line.toLowerCase() !== titleText)
                  .filter(line => !looksLikeUrl(line))
                  .filter(line => !/^(images|videos|news|maps|shopping|settings|tools)$/i.test(line));
              };
              const containers = Array.from(document.querySelectorAll(
                'article, .b_algo, .result, .web-result, .g, [data-testid="result"], [data-testid="web-result"], li'
              ));
              const rows = [];
              const seen = new Set();
              const candidates = containers.length ? containers : Array.from(document.querySelectorAll('a[href]')).map(a => a.parentElement || a);
              for (const container of candidates) {
                const links = Array.from(container.querySelectorAll ? container.querySelectorAll('a[href]') : []);
                const a = links.find(link => {
                  const text = cleanText(link.innerText || link.textContent || '');
                  return text.length >= 3 && !looksLikeUrl(text);
                }) || links[0];
                if (!a) continue;
                let href = decodeRedirect(a.href || '');
                let text = cleanText(a.innerText || a.textContent || '');
                if (href.startsWith('javascript:') || href.startsWith('#')) continue;
                try {
                  const url = new URL(href);
                  if (!/^https?:$/.test(url.protocol)) continue;
                  const host = url.hostname.replace(/^www\\./, '').toLowerCase();
                  if (blockedHosts.some(h => host === h || host.endsWith('.' + h))) continue;
                } catch (_) {
                  continue;
                }
                const key = href.split('#')[0];
                if (seen.has(key)) continue;
                seen.add(key);
                const heading = container.querySelector ? container.querySelector('h1,h2,h3') : null;
                const title = cleanText((heading && (heading.innerText || heading.textContent)) || text);
                if (!title || title.length < 3 || looksLikeUrl(title)) continue;
                const snippetNode = container.querySelector ? container.querySelector(
                  '[data-result="snippet"], [data-testid="result-snippet"], .b_caption p, .VwiC3b, .result__snippet, .snippet'
                ) : null;
                let snippet = cleanText(snippetNode && (snippetNode.innerText || snippetNode.textContent));
                if (!snippet || looksLikeUrl(snippet)) {
                  const lines = usefulLines(container.innerText || container.textContent || '', title);
                  snippet = lines.find(line => !line.includes(title)) || lines[0] || '';
                }
                let ref = a.getAttribute('data-hyw-ref') || '';
                if (!ref) {
                  ref = `result-${rows.length + 1}`;
                  a.setAttribute('data-hyw-ref', ref);
                }
                rows.push({
                  ref,
                  title: title.slice(0, 160),
                  url: href,
                  snippet: snippet.slice(0, 500)
                });
                if (rows.length >= 6) break;
              }
              return rows;
            })()
            """,
            as_expr=True,
        ) or []
    except Exception as exc:
        logger.debug(f"browser_action: search_results unavailable: {type(exc).__name__}: {exc}")
        return []

    results: List[Dict[str, str]] = []
    for row in raw_results:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        title = str(row.get("title") or "").strip()
        if not url or not title:
            continue
        result = {
            "title": title[:160],
            "url": url,
            "snippet": str(row.get("snippet") or "")[:300],
        }
        ref = str(row.get("ref") or "").strip()
        if ref:
            result["ref"] = ref
        results.append(result)
    return results


def _click_ref_with_js(tab: Any, ref: str) -> bool:
    try:
        ref_json = json.dumps(str(ref))
        return bool(tab.run_js(
            """
            (() => {
              const ref = %s;
              const el = document.querySelector('[data-hyw-ref=' + JSON.stringify(ref) + ']');
              if (!el) return false;
              el.scrollIntoView({block: 'center', inline: 'center'});
              const rect = el.getBoundingClientRect();
              const opts = {
                bubbles: true,
                cancelable: true,
                view: window,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2
              };
              for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                el.dispatchEvent(new MouseEvent(type, opts));
              }
              if (el instanceof HTMLAnchorElement && el.href) {
                window.location.href = el.href;
              }
              return true;
            })()
            """ % ref_json,
            as_expr=True,
        ))
    except Exception as exc:
        logger.debug(f"browser_action: JS ref click failed for {ref}: {type(exc).__name__}: {exc}")
        return False


def _list_tabs() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    stale: List[str] = []
    for tab_id, tab in _tabs.items():
        try:
            rows.append({
                "tab_id": tab_id,
                "active": tab_id == _active_tab_id,
                "title": str(tab.title or "")[:120],
                "url": str(tab.url or ""),
            })
        except Exception:
            stale.append(tab_id)
    for tab_id in stale:
        _tabs.pop(tab_id, None)
    return rows


def _annotate_interactive_elements(tab: Any) -> None:
    tab.run_js(
        """
        (() => {
          const existing = document.getElementById('hyw-ref-overlay-style');
          if (!existing) {
            const style = document.createElement('style');
            style.id = 'hyw-ref-overlay-style';
            style.textContent = `
              [data-hyw-ref] { outline: 2px solid rgba(255, 80, 80, .75) !important; outline-offset: 1px !important; }
              .hyw-ref-badge {
                position: fixed; z-index: 2147483647; pointer-events: none;
                background: #ff5050; color: white; font: 12px/1.2 sans-serif;
                padding: 2px 4px; border-radius: 4px;
              }
            `;
            document.head.appendChild(style);
          }
          document.querySelectorAll('.hyw-ref-badge').forEach(el => el.remove());
          const nodes = Array.from(document.querySelectorAll('a,button,input,textarea,select,label,[role="button"],[role="checkbox"],[tabindex],iframe,[contenteditable="true"]'))
            .filter(el => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            })
            .slice(0, 80);
          nodes.forEach((el, idx) => {
            const ref = String(idx + 1);
            el.setAttribute('data-hyw-ref', ref);
            if (idx < 25) {
              const rect = el.getBoundingClientRect();
              const badge = document.createElement('div');
              badge.className = 'hyw-ref-badge';
              badge.textContent = ref;
              badge.style.left = `${Math.max(0, rect.left)}px`;
              badge.style.top = `${Math.max(0, rect.top - 16)}px`;
              document.body.appendChild(badge);
            }
          });
        })();
        """
    )


def _detect_challenge(title: str, url: str, content: str, elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    text = "\n".join([
        title or "",
        url or "",
        content[:2000] if content else "",
        "\n".join(str(e.get("text", "")) for e in elements if isinstance(e, dict)),
    ]).lower()

    challenge_terms = [
        "captcha",
        "verify you are human",
        "unusual traffic",
        "机器人",
        "人机验证",
        "验证你是真人",
        "验证您是真人",
        "请验证您是真人",
        "请解决以下难题",
        "最后一步",
    ]
    if not any(term in text for term in challenge_terms):
        return None

    easy_terms = [
        "continue",
        "verify",
        "i'm not a robot",
        "i am not a robot",
        "继续",
        "验证",
        "我不是机器人",
        "验证您是真人",
        "请验证您是真人",
    ]
    easy_refs = [
        e.get("ref")
        for e in elements
        if isinstance(e, dict)
        and e.get("ref")
        and any(term in str(e.get("text", "")).lower() for term in easy_terms)
    ]

    return {
        "type": "verification_or_challenge",
        "message": "页面可能进入了搜索引擎验证。若 interactive_elements 中有明确的继续/验证/我不是机器人按钮，可以点击对应 ref；若是图形、滑块、选择题或需要解谜的验证码，应请用户在浏览器中手动完成后再继续操作当前 tab。",
        "easy_action_refs": easy_refs,
    }


def _json_error(message: str, tab: Optional[Any] = None, detail: str = "") -> str:
    payload: Dict[str, Any] = {"ok": False, "error": message}
    if detail:
        payload["detail"] = detail
    if tab is not None:
        try:
            payload.update({"title": tab.title, "url": tab.url})
        except Exception:
            pass
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\\\'", '.join(f"'{part}'" for part in parts) + ")"


def _build_search_url(query: str) -> str:
    engine = os.environ.get("HYW_SEARCH_ENGINE", "bing").strip().lower()
    encoded = quote(query)
    if engine == "duckduckgo":
        return f"https://duckduckgo.com/?q={encoded}"
    if engine == "google":
        return f"https://www.google.com/search?q={encoded}"
    return f"https://www.bing.com/search?q={encoded}"
