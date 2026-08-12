/***************************************************************************
 * Strict, allocation-bounded HUD advice extension for the Granite bridge.
 *
 * The optional wire tuple is `(coach BASE64URL)`.  BASE64URL is unpadded
 * and must decode to 1..31 printable ASCII bytes: exactly one TORCS driver
 * message line.  Standard SCR clients do not send this tuple and remain
 * compatible.
 ***************************************************************************/

#ifndef GRANITE_BRIDGE_COACH_PROTOCOL_H
#define GRANITE_BRIDGE_COACH_PROTOCOL_H

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <string>

namespace granite_protocol {

const std::string::size_type kHudLineBytes = 31;
const std::string::size_type kMaxEncodedHudBytes = 42;

enum CoachParseResult {
    COACH_INVALID = -1,
    COACH_ABSENT = 0,
    COACH_VALID = 1
};

inline int base64UrlValue(char character) {
    if (character >= 'A' && character <= 'Z') return character - 'A';
    if (character >= 'a' && character <= 'z') return character - 'a' + 26;
    if (character >= '0' && character <= '9') return character - '0' + 52;
    if (character == '-') return 62;
    if (character == '_') return 63;
    return -1;
}

inline bool decodeHudToken(const std::string &token, std::string *decoded) {
    if (decoded == 0 || token.empty() || token.size() > kMaxEncodedHudBytes ||
        token.size() % 4 == 1)
        return false;

    std::string output;
    unsigned int accumulator = 0;
    int bits = 0;
    for (std::string::size_type i = 0; i < token.size(); ++i) {
        const int value = base64UrlValue(token[i]);
        if (value < 0) return false;
        accumulator = (accumulator << 6) | (unsigned int)value;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            const unsigned char byte = (unsigned char)((accumulator >> bits) & 0xffU);
            if (byte < 0x20U || byte > 0x7eU || output.size() >= kHudLineBytes)
                return false;
            output.push_back((char)byte);
            accumulator = bits == 0 ? 0U : accumulator & ((1U << bits) - 1U);
        }
    }

    // Reject non-canonical encodings whose unused low bits are non-zero.
    if (accumulator != 0U || output.empty() ||
        token.size() != (output.size() * 8U + 5U) / 6U)
        return false;
    *decoded = output;
    return true;
}

inline CoachParseResult parseCoach(const std::string &message, std::string *advice) {
    const std::string prefix("(coach");
    const std::string::size_type at = message.find(prefix);
    if (at == std::string::npos) return COACH_ABSENT;

    const std::string::size_type valueAt = at + prefix.size();
    if (valueAt >= message.size() || message[valueAt] != ' ' ||
        message.find(prefix, valueAt) != std::string::npos)
        return COACH_INVALID;

    const std::string::size_type tokenAt = valueAt + 1;
    const std::string::size_type closeAt = message.find(')', tokenAt);
    if (closeAt == std::string::npos)
        return COACH_INVALID;
    const std::string token = message.substr(tokenAt, closeAt - tokenAt);
    return decodeHudToken(token, advice) ? COACH_VALID : COACH_INVALID;
}

struct ActionFields {
    float accel;
    float brake;
    float steer;
    float clutch;
    int gear;

    ActionFields()
        : accel(0.0f), brake(1.0f), steer(0.0f), clutch(0.0f), gear(1) {}
};

inline float clampFloat(float value, float low, float high) {
    return value < low ? low : (value > high ? high : value);
}

inline bool actionScalar(const std::string &message, const char *tag, double *result) {
    if (result == 0) return false;
    const std::string needle = std::string("(") + tag;
    std::string::size_type at = message.find(needle);
    if (at == std::string::npos) return false;
    at += needle.size();
    if (at >= message.size() || (message[at] != ' ' && message[at] != '\t'))
        return false;  // Reject prefixes such as "brakeBias" for "brake".
    while (at < message.size() && (message[at] == ' ' || message[at] == '\t')) ++at;

    const char *start = message.c_str() + at;
    char *end = 0;
    const double value = std::strtod(start, &end);
    if (end == start || !std::isfinite(value)) return false;
    while (*end == ' ' || *end == '\t') ++end;
    if (*end != ')') return false;  // A scalar tuple contains exactly one value.
    *result = value;
    return true;
}

inline bool parseActionPacket(const std::string &message, int maximumGear,
                              ActionFields *fields, std::string *advice) {
    if (fields == 0 || advice == 0 || maximumGear < 0) return false;
    double accel = 0.0, brake = 0.0, steer = 0.0, gearValue = 0.0;
    if (!actionScalar(message, "accel", &accel) ||
        !actionScalar(message, "brake", &brake) ||
        !actionScalar(message, "steer", &steer) ||
        !actionScalar(message, "gear", &gearValue))
        return false;
    if (gearValue < -1.0 || gearValue > (double)maximumGear ||
        std::floor(gearValue) != gearValue)
        return false;

    ActionFields parsed;
    parsed.accel = clampFloat((float)accel, 0.0f, 1.0f);
    parsed.brake = clampFloat((float)brake, 0.0f, 1.0f);
    parsed.steer = clampFloat((float)steer, -1.0f, 1.0f);
    double clutch = 0.0;
    if (actionScalar(message, "clutch", &clutch))
        parsed.clutch = clampFloat((float)clutch, 0.0f, 1.0f);
    parsed.gear = (int)gearValue;  // range and finiteness checked before conversion
    if (parsed.brake > 0.05f) parsed.accel = 0.0f;

    std::string parsedAdvice;
    if (parseCoach(message, &parsedAdvice) == COACH_VALID)
        *advice = parsedAdvice;
    else
        advice->clear();
    *fields = parsed;
    return true;
}

}  // namespace granite_protocol

#endif
