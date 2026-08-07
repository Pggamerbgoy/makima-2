// Makima v7.1 — Native Audio Service Stub (WASAPI)
//
// gRPC server implementing AudioCapture service + Health.Check.
// In production: captures PCM from WASAPI loopback.
// Stub: responds to Health.Check with SERVING, StartCapture returns empty stream.
//
// Build: cl /std:c++17 audio_service.cpp /link grpc++.lib protobuf.lib
// Or use CMake (see CMakeLists.txt)

#include <iostream>
#include <memory>
#include <string>
#include <thread>

// gRPC headers (would be generated from proto)
// #include "audio.grpc.pb.h"
// #include "health.grpc.pb.h"

// Stub implementation without actual gRPC compilation
// This serves as the reference implementation for when gRPC is set up

namespace makima {
namespace audio {

class AudioServiceImpl {
public:
    // Health.Check - always returns SERVING
    std::string CheckHealth() {
        return "SERVING";
    }

    // StartCapture - in production, opens WASAPI loopback
    // Returns PCM frames at configured sample rate
    bool StartCapture(int sample_rate, int channels) {
        std::cout << "[AudioService] StartCapture stub: "
                  << sample_rate << "Hz, " << channels << "ch\n";
        // Production: IAudioCaptureClient::GetBuffer() in a loop
        // Feeds into ring buffer accessible via gRPC stream
        return true;
    }

    bool StopCapture() {
        std::cout << "[AudioService] StopCapture\n";
        return true;
    }

    // GetDevices - enumerate audio devices
    // Production: IMMDeviceEnumerator::EnumAudioEndpoints
    std::string GetDevices() {
        return R"([{"id": "default", "name": "Default Device", "is_default": true}])";
    }
};

} // namespace audio
} // namespace makima

int main(int argc, char** argv) {
    std::cout << "[Makima] Audio Service starting on 127.0.0.1:50051\n";
    std::cout << "[Makima] Health: SERVING\n";
    std::cout << "[Makima] Stub mode — no actual WASAPI capture\n";

    makima::audio::AudioServiceImpl service;

    // In production: grpc::ServerBuilder, register service, BuildAndStart
    // For now: keep alive
    std::cout << "[Makima] Press Ctrl+C to stop\n";
    while (true) {
        std::this_thread::sleep_for(std::chrono::seconds(60));
    }

    return 0;
}
