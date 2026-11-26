#pragma once
#include <stdint.h>

enum class LedState : uint8_t {
    Idle, Scanning, Connecting, Connected, Streaming, Error, Reconnecting
};

inline bool led_on(LedState state, uint32_t elapsed) {
    switch (state) {
        case LedState::Idle: return elapsed % 3000 < 100;
        case LedState::Scanning: return elapsed % 400 < 200;
        case LedState::Connecting: {
            uint32_t phase = elapsed % 1000;
            return phase < 100 || (phase >= 200 && phase < 300);
        }
        case LedState::Connected: return true;
        case LedState::Streaming: return elapsed % 2000 < 1900;
        case LedState::Error: {
            uint32_t phase = elapsed % 1000;
            return phase < 100 || (phase >= 200 && phase < 300) ||
                   (phase >= 400 && phase < 500);
        }
        case LedState::Reconnecting: return elapsed % 1000 < 500;
    }
    return false;
}
