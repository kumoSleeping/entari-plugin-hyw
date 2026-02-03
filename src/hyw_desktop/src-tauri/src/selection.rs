// Platform-specific selected text retrieval

#[cfg(target_os = "macos")]
mod macos {
    use std::ffi::c_void;
    use std::ptr;
    use std::thread;
    use std::time::Duration;

    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXUIElementCreateSystemWide() -> *mut c_void;
        fn AXUIElementCreateApplication(pid: i32) -> *mut c_void;
        fn AXUIElementCopyAttributeValue(
            element: *mut c_void,
            attribute: *const c_void,
            value: *mut *mut c_void,
        ) -> i32;
        fn AXIsProcessTrusted() -> bool;
        fn CFRelease(cf: *mut c_void);
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFStringCreateWithCString(
            alloc: *mut c_void,
            cstr: *const i8,
            encoding: u32,
        ) -> *mut c_void;
        fn CFStringGetLength(theString: *mut c_void) -> isize;
        fn CFStringGetCString(
            theString: *mut c_void,
            buffer: *mut i8,
            bufferSize: isize,
            encoding: u32,
        ) -> bool;
    }

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGEventCreateKeyboardEvent(
            source: *mut c_void,
            virtual_key: u16,
            key_down: bool,
        ) -> *mut c_void;
        fn CGEventSetFlags(event: *mut c_void, flags: u64);
        fn CGEventPost(tap: u32, event: *mut c_void);
    }

    // NSWorkspace bindings via objc runtime
    #[link(name = "objc", kind = "dylib")]
    extern "C" {
        fn objc_getClass(name: *const i8) -> *mut c_void;
        fn sel_registerName(name: *const i8) -> *mut c_void;
        fn objc_msgSend(obj: *mut c_void, sel: *mut c_void, ...) -> *mut c_void;
    }

    const K_CF_STRING_ENCODING_UTF8: u32 = 0x08000100;
    const K_VK_C: u16 = 0x08; // Virtual key code for 'C'
    const K_CG_EVENT_FLAG_MASK_COMMAND: u64 = 1 << 20;
    const K_CG_HID_EVENT_TAP: u32 = 0;

    fn get_frontmost_app_pid() -> Option<i32> {
        unsafe {
            let workspace_class = objc_getClass(b"NSWorkspace\0".as_ptr() as *const i8);
            if workspace_class.is_null() {
                return None;
            }

            let shared_sel = sel_registerName(b"sharedWorkspace\0".as_ptr() as *const i8);
            let workspace = objc_msgSend(workspace_class, shared_sel);
            if workspace.is_null() {
                return None;
            }

            let frontmost_sel = sel_registerName(b"frontmostApplication\0".as_ptr() as *const i8);
            let app = objc_msgSend(workspace, frontmost_sel);
            if app.is_null() {
                return None;
            }

            let pid_sel = sel_registerName(b"processIdentifier\0".as_ptr() as *const i8);
            let pid = objc_msgSend(app, pid_sel) as isize as i32;
            Some(pid)
        }
    }

    unsafe fn get_focused_element_from_frontmost_app() -> Option<*mut c_void> {
        let pid = get_frontmost_app_pid()?;
        log::info!("[Selection] Frontmost app PID: {}", pid);

        let app_element = AXUIElementCreateApplication(pid);
        if app_element.is_null() {
            log::info!("[Selection] Failed to create AXUIElement for app");
            return None;
        }

        let focused_attr = CFStringCreateWithCString(
            ptr::null_mut(),
            b"AXFocusedUIElement\0".as_ptr() as *const i8,
            K_CF_STRING_ENCODING_UTF8,
        );
        if focused_attr.is_null() {
            CFRelease(app_element);
            return None;
        }

        let mut focused_element: *mut c_void = ptr::null_mut();
        let result = AXUIElementCopyAttributeValue(
            app_element,
            focused_attr,
            &mut focused_element,
        );

        CFRelease(focused_attr);
        CFRelease(app_element);

        if result != 0 || focused_element.is_null() {
            log::info!("[Selection] Failed to get focused element from app, error: {}", result);
            return None;
        }

        Some(focused_element)
    }

    unsafe fn get_focused_element_from_system_wide() -> Option<*mut c_void> {
        let system_wide = AXUIElementCreateSystemWide();
        if system_wide.is_null() {
            return None;
        }

        let focused_attr = CFStringCreateWithCString(
            ptr::null_mut(),
            b"AXFocusedUIElement\0".as_ptr() as *const i8,
            K_CF_STRING_ENCODING_UTF8,
        );
        if focused_attr.is_null() {
            CFRelease(system_wide);
            return None;
        }

        let mut focused_element: *mut c_void = ptr::null_mut();
        let result = AXUIElementCopyAttributeValue(
            system_wide,
            focused_attr,
            &mut focused_element,
        );

        CFRelease(focused_attr);
        CFRelease(system_wide);

        if result != 0 || focused_element.is_null() {
            log::info!("[Selection] Failed to get focused element from system-wide, error: {}", result);
            return None;
        }

        Some(focused_element)
    }

    /// Get clipboard string content using arboard
    fn get_clipboard_string() -> Option<String> {
        arboard::Clipboard::new()
            .ok()
            .and_then(|mut cb| cb.get_text().ok())
    }

    /// Set clipboard string content using arboard
    fn set_clipboard_string(text: &str) -> bool {
        arboard::Clipboard::new()
            .ok()
            .and_then(|mut cb| cb.set_text(text.to_string()).ok())
            .is_some()
    }

    /// Simulate Cmd+C keystroke
    fn simulate_cmd_c() {
        unsafe {
            // Key down with Command modifier
            let key_down = CGEventCreateKeyboardEvent(ptr::null_mut(), K_VK_C, true);
            if !key_down.is_null() {
                CGEventSetFlags(key_down, K_CG_EVENT_FLAG_MASK_COMMAND);
                CGEventPost(K_CG_HID_EVENT_TAP, key_down);
                CFRelease(key_down);
            }

            // Small delay between key down and up
            thread::sleep(Duration::from_millis(10));

            // Key up with Command modifier
            let key_up = CGEventCreateKeyboardEvent(ptr::null_mut(), K_VK_C, false);
            if !key_up.is_null() {
                CGEventSetFlags(key_up, K_CG_EVENT_FLAG_MASK_COMMAND);
                CGEventPost(K_CG_HID_EVENT_TAP, key_up);
                CFRelease(key_up);
            }
        }
    }

    /// Fallback: get selected text via Cmd+C
    fn get_selected_text_via_clipboard() -> Option<String> {
        // Wrap in catch_unwind to prevent panics from aborting
        std::panic::catch_unwind(|| {
            log::info!("[Selection] Trying clipboard fallback (Cmd+C)");

            // Save original clipboard content
            let original_clipboard = get_clipboard_string();
            log::info!("[Selection] Original clipboard saved: {:?}", original_clipboard.is_some());

            // Clear clipboard first
            let _ = set_clipboard_string("");

            // Small delay to ensure clipboard is cleared
            thread::sleep(Duration::from_millis(20));

            // Simulate Cmd+C
            simulate_cmd_c();

            // Wait for copy to complete
            thread::sleep(Duration::from_millis(50));

            // Get new clipboard content
            let new_clipboard = get_clipboard_string();
            log::info!("[Selection] New clipboard: {:?}", new_clipboard);

            // Restore original clipboard
            if let Some(ref original) = original_clipboard {
                // Only restore if we got something different (meaning a selection was copied)
                if new_clipboard.as_ref() != Some(original) && new_clipboard.is_some() {
                    // Delay before restore to avoid race
                    thread::sleep(Duration::from_millis(20));
                    let _ = set_clipboard_string(original);
                    log::info!("[Selection] Restored original clipboard");
                }
            }

            // Return new content only if it's not empty
            new_clipboard.filter(|s| !s.is_empty())
        })
        .ok()
        .flatten()
    }

    /// Try to get selected text using Accessibility API
    unsafe fn get_selected_text_via_accessibility() -> Option<String> {
        // Try to get focused element from frontmost application first
        let focused_element = get_focused_element_from_frontmost_app()
            .or_else(|| get_focused_element_from_system_wide());

        let focused_element = match focused_element {
            Some(el) => el,
            None => {
                log::info!("[Selection] Could not get focused element from any source");
                return None;
            }
        };

        log::info!("[Selection] Got focused element");

        // Get selected text
        let selected_text_attr = CFStringCreateWithCString(
            ptr::null_mut(),
            b"AXSelectedText\0".as_ptr() as *const i8,
            K_CF_STRING_ENCODING_UTF8,
        );
        if selected_text_attr.is_null() {
            log::error!("[Selection] Failed to create AXSelectedText string");
            CFRelease(focused_element);
            return None;
        }

        let mut selected_text: *mut c_void = ptr::null_mut();
        let result = AXUIElementCopyAttributeValue(
            focused_element,
            selected_text_attr,
            &mut selected_text,
        );

        CFRelease(selected_text_attr);
        CFRelease(focused_element);

        if result != 0 {
            log::info!("[Selection] No selected text attribute, error code: {}", result);
            return None;
        }
        if selected_text.is_null() {
            log::info!("[Selection] Selected text is null");
            return None;
        }

        // Convert CFString to Rust String
        let length = CFStringGetLength(selected_text);
        log::info!("[Selection] Selected text length: {}", length);
        if length <= 0 {
            CFRelease(selected_text);
            return None;
        }

        let buffer_size = (length * 4 + 1) as usize; // UTF-8 can be up to 4 bytes per char
        let mut buffer: Vec<i8> = vec![0; buffer_size];

        let success = CFStringGetCString(
            selected_text,
            buffer.as_mut_ptr(),
            buffer_size as isize,
            K_CF_STRING_ENCODING_UTF8,
        );

        CFRelease(selected_text);

        if !success {
            log::error!("[Selection] Failed to convert CFString to C string");
            return None;
        }

        // Convert to Rust String
        let c_str = std::ffi::CStr::from_ptr(buffer.as_ptr());
        let result = c_str.to_str().ok().map(|s| s.to_string());
        log::info!("[Selection] Accessibility result: {:?}", result);
        result
    }

    pub fn get_selected_text() -> Option<String> {
        unsafe {
            // Check if we have accessibility permission
            let trusted = AXIsProcessTrusted();
            log::info!("[Selection] AXIsProcessTrusted: {}", trusted);
            if !trusted {
                log::warn!("[Selection] App is NOT trusted for accessibility. Please grant permission in System Settings > Privacy & Security > Accessibility");
                return None;
            }

            // Strategy 1: Try Accessibility API first
            if let Some(text) = get_selected_text_via_accessibility() {
                if !text.is_empty() {
                    log::info!("[Selection] Got text via Accessibility API");
                    return Some(text);
                }
            }

            // Strategy 2: Fallback to Cmd+C clipboard method
            if let Some(text) = get_selected_text_via_clipboard() {
                log::info!("[Selection] Got text via clipboard fallback");
                return Some(text);
            }

            log::info!("[Selection] No text obtained from any method");
            None
        }
    }
}

#[cfg(target_os = "windows")]
mod windows {
    pub fn get_selected_text() -> Option<String> {
        None
    }
}

#[cfg(target_os = "linux")]
mod linux {
    pub fn get_selected_text() -> Option<String> {
        None
    }
}

/// Get the currently selected text from the focused application.
/// Returns None if no text is selected or if access is denied.
///
/// On macOS, requires Accessibility permissions in System Settings.
pub fn get_selected_text() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        macos::get_selected_text()
    }
    #[cfg(target_os = "windows")]
    {
        windows::get_selected_text()
    }
    #[cfg(target_os = "linux")]
    {
        linux::get_selected_text()
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        None
    }
}
