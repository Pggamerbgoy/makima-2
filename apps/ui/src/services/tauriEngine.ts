/**
 * Makima v7.1 — Tauri IPC Service
 *
 * Wraps Rust IPC calls defined in `src-tauri/src/lib.rs`.
 * Provides safe fallback when running in a standard web browser without Tauri.
 */

import { invoke } from "@tauri-apps/api/core";

export async function isTauriEnvironment(): Promise<boolean> {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function startTauriEngine(): Promise<string> {
  if (await isTauriEnvironment()) {
    try {
      return await invoke<string>("start_engine");
    } catch (e) {
      console.error("[Tauri IPC] start_engine failed:", e);
      return `Failed to start engine: ${e}`;
    }
  }
  console.log("[Tauri IPC] Fallback: Simulated engine start in web mode.");
  return "Simulated engine start (Web Mode)";
}

export async function stopTauriEngine(): Promise<string> {
  if (await isTauriEnvironment()) {
    try {
      return await invoke<string>("stop_engine");
    } catch (e) {
      console.error("[Tauri IPC] stop_engine failed:", e);
      return `Failed to stop engine: ${e}`;
    }
  }
  console.log("[Tauri IPC] Fallback: Simulated engine stop in web mode.");
  return "Simulated engine stop (Web Mode)";
}

export async function checkTauriEngineStatus(): Promise<boolean> {
  if (await isTauriEnvironment()) {
    try {
      return await invoke<boolean>("is_engine_running");
    } catch (e) {
      console.error("[Tauri IPC] is_engine_running failed:", e);
      return false;
    }
  }
  return true;
}

export async function toggleTauriOverlay(): Promise<void> {
  if (await isTauriEnvironment()) {
    try {
      await invoke("toggle_overlay");
      return;
    } catch (e) {
      console.error("[Tauri IPC] toggle_overlay failed:", e);
    }
  } else {
    window.open("/?window=overlay", "MakimaOverlay", "width=600,height=500,menubar=no,toolbar=no,location=no,status=no");
  }
}
