#include <cassert>
#include <string>

#include "coach_protocol.h"

int main() {
    using granite_protocol::COACH_ABSENT;
    using granite_protocol::COACH_INVALID;
    using granite_protocol::COACH_VALID;
    using granite_protocol::ActionFields;
    using granite_protocol::parseActionPacket;
    using granite_protocol::parseCoach;

    std::string advice;
    assert(parseCoach("(accel 1)(brake 0)", &advice) == COACH_ABSENT);
    assert(parseCoach("(accel 1)(coach QnJha2Ugbm93)(brake 0)", &advice) == COACH_VALID);
    assert(advice == "Brake now");
    assert(parseCoach("(coach QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQQ)", &advice) ==
           COACH_VALID);
    assert(advice == std::string(31, 'A'));

    assert(parseCoach("(coach )", &advice) == COACH_INVALID);
    assert(parseCoach("(coach QnJha2Ugbm93=)", &advice) == COACH_INVALID);
    assert(parseCoach("(coach Zh)", &advice) == COACH_INVALID);  // non-zero unused bits
    assert(parseCoach("(coach Cg)", &advice) == COACH_INVALID);  // decoded newline
    assert(parseCoach("(coachevil QnJha2Ugbm93)", &advice) == COACH_INVALID);
    assert(parseCoach("(coach QnJha2Ugbm93)(coach QnJha2Ugbm93)", &advice) ==
           COACH_INVALID);
    assert(parseCoach("(coach QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE)", &advice) ==
           COACH_INVALID);  // 32 decoded bytes

    ActionFields controls;
    const std::string safe =
        "(accel 0.8)(brake 0)(gear 3)(steer -0.25)(clutch 0.1)(focus 0)(meta 0)";
    assert(parseActionPacket(safe + "(coach QnJha2Ugbm93)", 7, &controls, &advice));
    assert(controls.accel == 0.8f && controls.brake == 0.0f);
    assert(controls.steer == -0.25f && controls.clutch == 0.1f && controls.gear == 3);
    assert(advice == "Brake now");

    // Malformed or absent display data clears old text without rejecting controls.
    advice = "old instruction";
    assert(parseActionPacket(safe + "(coach Cg)", 7, &controls, &advice));
    assert(advice.empty() && controls.accel == 0.8f && controls.gear == 3);
    advice = "old instruction";
    assert(parseActionPacket(safe, 7, &controls, &advice));
    assert(advice.empty() && controls.steer == -0.25f);

    assert(parseActionPacket(
        "(accel 2)(brake 0.2)(gear 7)(steer -4)(clutch 2)",
        7, &controls, &advice));
    assert(controls.accel == 0.0f && controls.brake == 0.2f);
    assert(controls.steer == -1.0f && controls.clutch == 1.0f && controls.gear == 7);
    assert(!parseActionPacket(
        "(accel 1)(brake 0)(gear 1e100)(steer 0)", 7, &controls, &advice));
    assert(!parseActionPacket(
        "(accel 1evil)(brake 0)(gear 1)(steer 0)", 7, &controls, &advice));
    assert(!parseActionPacket("(accel 1)(brake 0)(gear 1)", 7, &controls, &advice));
    return 0;
}
