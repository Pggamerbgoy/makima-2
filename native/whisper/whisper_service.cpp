// Makima v7.1 — Native Whisper Service Stub (whisper.cpp)
// gRPC server wrapping whisper.cpp for Speech-to-Text.
// Stub: returns "[Transcription Placeholder]" for any audio input.

#include <iostream>
#include <string>
#include <thread>

namespace makima {
namespace whisper {

class WhisperServiceImpl {
public:
    std::string CheckHealth() { return "SERVING"; }

    // TranscribeAudio - in production: feeds PCM to whisper.cpp
    // model: tiny/base/small/medium for speed/quality tradeoff
    std::string Transcribe(const char* audio_data, size_t len,
                           const std::string& model = "base",
                           const std::string& language = "en") {
        std::cout << "[WhisperService] Transcribe stub: " << len
                  << " bytes, model=" << model << "\n";
        // Production: whisper_full() with whisper_full_params
        return "[Transcription Placeholder]";
    }

    // ListModels - available whisper models
    std::string ListModels() {
        return R"(["tiny", "base", "small", "medium"])";
    }
};

} // namespace whisper
} // namespace makima

int main() {
    std::cout << "[Makima] Whisper STT Service on 127.0.0.1:50052\n";
    std::cout << "[Makima] Stub mode\n";
    while (true) { std::this_thread::sleep_for(std::chrono::seconds(60)); }
    return 0;
}
