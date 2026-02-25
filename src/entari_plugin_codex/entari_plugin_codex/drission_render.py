from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage.errors import PageDisconnectedError
from loguru import logger
from PIL import Image


def _resolve_template_path() -> Path:
    candidates: list[Path] = []

    env_path = os.getenv("CODEX_CARD_TEMPLATE")
    if env_path:
        candidates.append(Path(env_path).expanduser())

    here = Path(__file__).resolve()
    candidates.append(here.parent / "assets" / "card-dist" / "index.html")
    candidates.append(
        here.parents[2]
        / "entari_plugin_hyw"
        / "entari_plugin_hyw"
        / "core"
        / "tools"
        / "_public"
        / "browser"
        / "assets"
        / "card-dist"
        / "index.html"
    )

    try:
        dist = metadata.distribution("entari-plugin-hyw")
        candidates.append(
            Path(
                dist.locate_file(
                    "entari_plugin_hyw/core/tools/_public/browser/assets/card-dist/index.html"
                )
            )
        )
    except Exception:
        pass

    for candidate in candidates:
        path = candidate.resolve()
        if path.exists():
            return path

    checked = "\n".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Card template not found. Reuse hyw frontend build first "
        f"(expected card-dist/index.html). Checked:\n{checked}"
    )


def _compress_image_b64(b64_data: str, quality: int = 90, max_width: int = 2160) -> str:
    img_bytes = base64.b64decode(b64_data)
    image = Image.open(io.BytesIO(img_bytes))

    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.Resampling.LANCZOS)

    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("utf-8")


class SharedBrowserManager:
    _instance: Optional["SharedBrowserManager"] = None
    _class_lock = threading.Lock()

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._page: Optional[ChromiumPage] = None
        self._starting = False
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls, headless: bool = True) -> "SharedBrowserManager":
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls(headless=headless)
            return cls._instance

    def start(self) -> bool:
        if self._page is not None:
            try:
                if self._page.run_cdp("Browser.getVersion"):
                    return True
            except (PageDisconnectedError, Exception):
                self._page = None

        with self._lock:
            if self._starting:
                return False
            self._starting = True

        try:
            co = ChromiumOptions()
            co.headless(True)  # hard-coded headless for codex renderer
            co.auto_port()
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-gpu")
            co.set_argument("--allow-file-access-from-files")
            co.set_argument("--disable-web-security")
            co.set_argument("--hide-scrollbars")
            co.set_argument("--window-size=1440,900")

            self._page = ChromiumPage(addr_or_opts=co)
            logger.success("Codex DrissionPage browser ready (port={})", self._page.address)
            return True
        finally:
            self._starting = False

    @property
    def page(self) -> ChromiumPage:
        if self._page is None:
            self.start()
        if self._page is None:
            raise RuntimeError("Drission browser unavailable")
        return self._page

    def new_tab(self, url: str):
        return self.page.new_tab(url)

    def close(self):
        with self._lock:
            if self._page:
                try:
                    self._page.quit()
                except Exception as exc:
                    logger.warning("Codex browser close failed: {}", exc)
                finally:
                    self._page = None

    @staticmethod
    def hide_scrollbars(tab: Any) -> None:
        try:
            tab.run_cdp("Emulation.setScrollbarsHidden", hidden=True)
            tab.run_js(
                """
                const style = document.createElement('style');
                style.textContent = `
                    ::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
                    * { -ms-overflow-style: none !important; scrollbar-width: none !important; }
                `;
                document.head.appendChild(style);
                """
            )
        except Exception as exc:
            logger.debug("Hide scrollbars failed: {}", exc)


class DrissionRenderer:
    def __init__(self, headless: bool = True):
        self.template_path = _resolve_template_path()
        self._manager = SharedBrowserManager.get_instance(headless=headless)
        self._executor = ThreadPoolExecutor(max_workers=6)

    async def start(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._manager.start)
        await self._warmup_tab()

    async def _warmup_tab(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._warmup_tab_sync)

    def _warmup_tab_sync(self):
        tab = None
        try:
            tab = self._manager.new_tab(self.template_path.as_uri())
            tab.ele("#app", timeout=6)
            payload = {
                "markdown": "# Ready",
                "total_time": 0,
                "stages": [],
                "references": [],
                "page_references": [],
                "image_references": [],
                "stats": {},
                "theme_color": "#ef4444",
            }
            tab.run_js(f"window.updateRenderData({json.dumps(payload, ensure_ascii=False)})")
            self._wait_for_render_finished(tab, timeout=10.0, context="warmup")
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass

    def _wait_for_render_finished(self, tab: Any, timeout: float = 12.0, context: str = ""):
        import time

        start = time.time()
        try:
            if tab.run_js("return window.RENDER_FINISHED"):
                tab.run_js("window.RENDER_FINISHED = false")
        except Exception:
            pass

        while time.time() - start < timeout:
            try:
                if tab.run_js("return window.RENDER_FINISHED"):
                    return True
            except Exception:
                pass
            time.sleep(0.1)

        logger.warning("Codex renderer wait timeout: {}", context)
        return False

    async def render_to_base64(
        self,
        markdown_content: str,
        title: str = "Codex",
        theme_color: str = "#ef4444",
        stats: Optional[Dict[str, Any]] = None,
        total_time: float = 0.0,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._render_to_base64_sync,
            markdown_content,
            title,
            theme_color,
            stats,
            total_time,
        )

    def _render_to_base64_sync(
        self,
        markdown_content: str,
        title: str,
        theme_color: str,
        stats: Optional[Dict[str, Any]] = None,
        total_time: float = 0.0,
    ) -> str:
        tab = None
        try:
            tab = self._manager.new_tab(self.template_path.as_uri())
            tab.ele("#app", timeout=6)

            stats_payload = stats or {}
            elapsed = float(total_time or stats_payload.get("total_time") or 0.0)

            render_data = {
                "markdown": markdown_content,
                "total_time": elapsed,
                "stages": [],
                "references": [],
                "page_references": [],
                "image_references": [],
                "stats": stats_payload,
                "theme_color": theme_color,
                "title": title,
            }
            tab.run_js(f"window.updateRenderData({json.dumps(render_data, ensure_ascii=False)})")
            self._wait_for_render_finished(tab, timeout=12.0, context="render")

            scroll_height = tab.run_js(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
            )
            viewport_height = int(scroll_height) + 220
            tab.run_cdp(
                "Emulation.setDeviceMetricsOverride",
                width=1920,
                height=viewport_height,
                deviceScaleFactor=2,
                mobile=False,
            )

            SharedBrowserManager.hide_scrollbars(tab)
            tab.run_js('document.documentElement.style.overflow = "hidden";')
            tab.run_js('document.body.style.overflow = "hidden";')
            tab.run_js('document.documentElement.style.scrollbarGutter = "unset";')
            tab.run_js('document.documentElement.style.width = "100%";')

            main_ele = tab.ele("#main-container", timeout=5)
            if main_ele:
                b64_img = main_ele.get_screenshot(as_base64="jpg")
            else:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    out_path = Path(tf.name)
                tab.get_screenshot(path=str(out_path.parent), name=out_path.name, full_page=True)
                b64_img = base64.b64encode(out_path.read_bytes()).decode("utf-8")
                out_path.unlink(missing_ok=True)

            return _compress_image_b64(b64_img, quality=90, max_width=2160)
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass

    async def close(self):
        self._executor.shutdown(wait=False)


_renderer: Optional[DrissionRenderer] = None
_renderer_lock = asyncio.Lock()


async def get_drission_renderer(headless: bool = True) -> DrissionRenderer:
    global _renderer
    if _renderer is not None:
        return _renderer
    async with _renderer_lock:
        if _renderer is None:
            _renderer = DrissionRenderer(headless=headless)
            await _renderer.start()
        return _renderer


async def warmup_drission_renderer(headless: bool = True) -> None:
    await get_drission_renderer(headless=headless)


async def close_drission_renderer() -> None:
    global _renderer
    if _renderer is not None:
        await _renderer.close()
        _renderer = None
    SharedBrowserManager.get_instance(headless=True).close()


async def render_markdown_to_base64_drission(
    markdown_content: str,
    title: str = "Codex",
    theme_color: str = "#ef4444",
    stats: Optional[Dict[str, Any]] = None,
    total_time: float = 0.0,
    headless: bool = True,
) -> str:
    renderer = await get_drission_renderer(headless=headless)
    return await renderer.render_to_base64(
        markdown_content=markdown_content,
        title=title,
        theme_color=theme_color,
        stats=stats,
        total_time=total_time,
    )
