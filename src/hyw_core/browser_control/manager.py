"""
Shared Browser Manager (DrissionPage)

Manages a single ChromiumPage browser instance.
Supports multi-tab concurrency via DrissionPage's built-in tab management.
"""

import threading
from typing import Optional, Any
from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError

class SharedBrowserManager:
    """
    Manages a shared DrissionPage Chromium browser.
    """
    
    _instance: Optional["SharedBrowserManager"] = None
    _lock = threading.Lock()
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._page: Optional[ChromiumPage] = None
        self._starting = False
        self._tab_lock = threading.Lock()
    
    @classmethod
    def get_instance(cls, headless: bool = True) -> "SharedBrowserManager":
        """Get or create the singleton SharedBrowserManager."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(headless=headless)
            return cls._instance
    
    def start(self) -> bool:
        """Start the Chromium browser."""
        if self._page is not None:
            # Check if alive
            try:
                if self._page.run_cdp('Browser.getVersion'):
                    return True
            except (PageDisconnectedError, Exception):
                self._page = None
        
        with self._lock:
            if self._starting:
                return False
            self._starting = True
        
        try:
            logger.info("SharedBrowserManager: Starting DrissionPage browser...")
            
            # Configure options
            co = ChromiumOptions()
            co.headless(self.headless)
            co.auto_port()  # Auto find available port
            
            # Anti-detection settings 
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-gpu')
            # Essential for loading local files and avoiding CORS issues
            co.set_argument('--allow-file-access-from-files')
            co.set_argument('--disable-web-security')
            # Hide scrollbars globally
            co.set_argument('--hide-scrollbars')
            # 十万的原因是滚动条屏蔽(大概吧)
            co.set_argument('--window-size=1280,800')
            self._page = ChromiumPage(addr_or_opts=co)
            
            # Show Landing Page
            try:
                import os
                landing_path = os.path.join(os.path.dirname(__file__), 'landing.html')
                if os.path.exists(landing_path):
                    self._page.get(f"file://{landing_path}")
            except Exception as e:
                logger.warning(f"SharedBrowserManager: Failed to show landing page: {e}")

            logger.success(f"SharedBrowserManager: Browser ready (port={self._page.address})")
            return True
            
        except Exception as e:
            logger.error(f"SharedBrowserManager: Failed to start browser: {e}")
            self._page = None
            raise
        finally:
            self._starting = False
    
    @property
    def page(self) -> Optional[ChromiumPage]:
        """Get the main ChromiumPage instance."""
        if self._page is None:
            self.start()
        return self._page

    def new_tab(self, url: str = None) -> Any:
        """
        Thread-safe tab creation.
        DrissionPage is thread-safe for tab creation, so we call it directly
        to allow atomic creation+navigation (Target.createTarget with url)
        without blocking other threads.
        """
        page = self.page
        if not page:
             raise RuntimeError("Browser not available")
        
        # Direct call allows Chrome to handle creation and navigation atomically and concurrently
        return page.new_tab(url)

    
    def close(self):
        """Shutdown the browser."""
        with self._lock:
            if self._page:
                try:
                    self._page.quit()
                    logger.info("SharedBrowserManager: Browser closed.")
                except Exception as e:
                    logger.warning(f"SharedBrowserManager: Error closing browser: {e}")
                finally:
                    self._page = None

    @staticmethod
    def hide_scrollbars(page: ChromiumPage):
        """
        Robustly hide scrollbars using CDP commands.
        This eliminates the reserved space/gutter that standard CSS might miss.
        """
        try:
            # Emulation.setScrollbarsHidden is a CDP command
            page.run_cdp('Emulation.setScrollbarsHidden', hidden=True)
            logger.debug("SharedBrowserManager: CDP scrollbars hidden.")
        except Exception as e:
            logger.warning(f"SharedBrowserManager: Failed to hide scrollbars via CDP: {e}")


# Module-level singleton accessor
_shared_manager: Optional[SharedBrowserManager] = None


def get_shared_browser_manager(headless: bool = True) -> SharedBrowserManager:
    """
    Get or create the shared browser manager.
    """
    global _shared_manager
    
    if _shared_manager is None:
        _shared_manager = SharedBrowserManager.get_instance(headless=headless)
        _shared_manager.start()
    
    return _shared_manager


def close_shared_browser():
    """Close the shared browser manager."""
    global _shared_manager
    if _shared_manager:
        _shared_manager.close()
        _shared_manager = None
