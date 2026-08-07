// Makima v7.1 — Clipboard Watcher Service Stub
// gRPC server monitoring WM_CLIPBOARDUPDATE events.
// Stub: returns empty clipboard, no streaming.

#include <iostream>
#include <string>
#include <thread>

namespace makima {
namespace clipboard {

class ClipboardServiceImpl {
public:
    std::string CheckHealth() { return "SERVING"; }

    // GetClipboard - read current clipboard text
    std::string GetClipboard(int max_chars = 4000) {
        // Production: OpenClipboard → GetClipboardData(CF_UNICODETEXT)
        return "";
    }

    // StreamClipboardChanges - push updates when clipboard changes
    // Production: AddClipboardFormatListener + message loop
    void StartMonitoring() {
        std::cout << "[ClipboardService] Monitoring started (stub)\n";
    }
};

} // namespace clipboard
} // namespace makima

int main() {
    std::cout << "[Makima] Clipboard Service on 127.0.0.1:50056\n";
    std::cout << "[Makima] Stub mode\n";
    while (true) { std::this_thread::sleep_for(std::chrono::seconds(60)); }
    return 0;
}
