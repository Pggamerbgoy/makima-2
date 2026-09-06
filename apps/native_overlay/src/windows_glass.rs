//! Windows-only backdrop setup for the native overlay.
//!
//! Win11 exposes a supported system backdrop attribute. Older Windows builds
//! do not, so we still extend the DWM frame across the client area and let the
//! Slint-painted surface provide the opaque fallback.

use raw_window_handle::{HasWindowHandle, RawWindowHandle};
use slint::Window;
use windows::core::BOOL;
use windows::Win32::Foundation::HWND;
use windows::Win32::Graphics::Dwm::{
    DwmExtendFrameIntoClientArea, DwmSetWindowAttribute, DWMSBT_TRANSIENTWINDOW,
    DWMWA_SYSTEMBACKDROP_TYPE,
};
use windows::Win32::UI::Controls::MARGINS;

#[repr(C)]
struct AccentPolicy {
    accent_state: i32,
    accent_flags: u32,
    gradient_color: u32,
    animation_id: u32,
}

#[repr(C)]
struct WindowCompositionAttributeData {
    attribute: i32,
    data: *mut core::ffi::c_void,
    data_size: u32,
}

const WCA_ACCENT_POLICY: i32 = 19;
const ACCENT_ENABLE_ACRYLICBLURBEHIND: i32 = 4;

#[link(name = "user32")]
unsafe extern "system" {
    fn SetWindowCompositionAttribute(hwnd: HWND, data: *mut WindowCompositionAttributeData)
        -> BOOL;
}

pub fn apply(window: &Window) {
    let handle = window.window_handle();
    let Ok(raw) = handle.window_handle() else {
        return;
    };
    let RawWindowHandle::Win32(win32) = raw.as_raw() else {
        return;
    };

    let hwnd = HWND(win32.hwnd.get() as *mut core::ffi::c_void);
    unsafe {
        // Supported on Windows 11 22H2+. On Windows 10 this returns an error;
        // the frame extension below remains the compatible fallback.
        let backdrop = DWMSBT_TRANSIENTWINDOW.0;
        let backdrop_result = DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            (&backdrop as *const i32).cast(),
            core::mem::size_of::<i32>() as u32,
        );

        // Windows 10 has no SYSTEMBACKDROP_TYPE. Use its legacy composition
        // attribute only when the supported Win11 backdrop is unavailable.
        if backdrop_result.is_err() {
            let mut policy = AccentPolicy {
                accent_state: ACCENT_ENABLE_ACRYLICBLURBEHIND,
                accent_flags: 0,
                // AABBGGRR: translucent Makima obsidian-violet.
                gradient_color: 0xCC221317,
                animation_id: 0,
            };
            let mut data = WindowCompositionAttributeData {
                attribute: WCA_ACCENT_POLICY,
                data: (&mut policy as *mut AccentPolicy).cast(),
                data_size: core::mem::size_of::<AccentPolicy>() as u32,
            };
            let _ = SetWindowCompositionAttribute(hwnd, &mut data);
        }

        // Extend DWM into the client area on both Win10 and Win11 so the
        // transparent Slint window gets the native glass backdrop when the OS
        // supports it, while remaining visually safe on older builds.
        let margins = MARGINS {
            cxLeftWidth: -1,
            cxRightWidth: -1,
            cyTopHeight: -1,
            cyBottomHeight: -1,
        };
        let _ = DwmExtendFrameIntoClientArea(hwnd, &margins);
    }
}
