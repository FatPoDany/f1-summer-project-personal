/***************************************************************************
 * Minimal non-blocking SCR-compatible bridge for TORCS 1.3.9.
 * GPL-2.0-or-later when distributed with the reused SCR sensor helpers.
 ***************************************************************************/

#include <arpa/inet.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <string>

#include <tgf.h>
#include <track.h>
#include <car.h>
#include <raceman.h>
#include <robottools.h>
#include <robot.h>

#include "sensors.h"
#include "ObstacleSensors.h"
#include "coach_protocol.h"

namespace {

const int kPort = 3001;
const int kTrackCount = 19;
const int kOpponentCount = 36;
const int kRange = 200;
const int kMaxPacketsPerTick = 32;
const unsigned long kActionTtlTicks = 12;  // about 240 ms at 50 Hz

struct Action {
    float accel;
    float brake;
    float steer;
    float clutch;
    int gear;

    Action() : accel(0.0f), brake(1.0f), steer(0.0f), clutch(0.0f), gear(1) {}
};

struct Bridge {
    int fd;
    sockaddr_in peer;
    socklen_t peerLen;
    bool identified;
    unsigned long tick;
    unsigned long actionTick;
    float angles[kTrackCount];
    Action action;
    std::string advice;
    Sensors *trackSensors;
    ObstacleSensors *opponentSensors;

    Bridge()
        : fd(-1), peerLen(sizeof(peer)), identified(false), tick(0),
          actionTick(0), trackSensors(NULL), opponentSensors(NULL) {
        std::memset(&peer, 0, sizeof(peer));
        for (int i = 0; i < kTrackCount; ++i) angles[i] = -90.0f + 10.0f * i;
    }
};

tTrack *gTrack = NULL;
Bridge gBridge;
std::string gExpectedBotId;

bool samePeer(const sockaddr_in &a, const sockaddr_in &b) {
    return a.sin_family == b.sin_family && a.sin_port == b.sin_port &&
           a.sin_addr.s_addr == b.sin_addr.s_addr;
}

bool parseInit(const std::string &message, float parsed[kTrackCount]) {
    const std::string needle("(init");
    const std::string::size_type at = message.find(needle);
    if (at == std::string::npos || at < 3)
        return false;
    const std::string botId = message.substr(0, at);
    if (!gExpectedBotId.empty()) {
        if (botId != gExpectedBotId) return false;
    } else {
        if (botId.compare(0, 3, "SCR") != 0) return false;
        for (std::string::size_type i = 3; i < botId.size(); ++i) {
            const unsigned char character = (unsigned char)botId[i];
            if (std::isspace(character) || character == '(' || character == ')')
                return false;
        }
    }
    std::istringstream input(message.substr(at + needle.size()));
    for (int i = 0; i < kTrackCount; ++i) {
        if (!(input >> parsed[i]) || !std::isfinite(parsed[i]) ||
            parsed[i] < -180.0f || parsed[i] > 180.0f)
            return false;
    }
    input >> std::ws;
    char closing = '\0';
    if (!input.get(closing) || closing != ')') return false;
    input >> std::ws;
    return input.eof();
}

bool parseAction(const std::string &message, tCarElt *car) {
    granite_protocol::ActionFields parsed;
    std::string advice;
    const int maximum = car->_gearNb - 1;
    if (!granite_protocol::parseActionPacket(message, maximum, &parsed, &advice))
        return false;

    Action next;
    next.accel = parsed.accel;
    next.brake = parsed.brake;
    next.steer = parsed.steer;
    next.clutch = parsed.clutch;
    next.gear = parsed.gear;
    // The helper validates controls independently from the optional display
    // tuple. Missing or malformed advice clears the prior HUD line.
    gBridge.advice = advice;

    gBridge.action = next;
    gBridge.actionTick = gBridge.tick;
    return true;
}

void configureTrackSensors(tCarElt *car) {
    delete gBridge.trackSensors;
    gBridge.trackSensors = new Sensors(car, kTrackCount);
    for (int i = 0; i < kTrackCount; ++i)
        gBridge.trackSensors->setSensor(i, gBridge.angles[i], kRange);
}

void receivePackets(tCarElt *car) {
    if (gBridge.fd < 0) return;
    for (int packet = 0; packet < kMaxPacketsPerTick; ++packet) {
        char buffer[2048];
        sockaddr_in source;
        socklen_t sourceLen = sizeof(source);
        const ssize_t size = recvfrom(gBridge.fd, buffer, sizeof(buffer) - 1, 0,
                                      (sockaddr *)&source, &sourceLen);
        if (size < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK)
                std::fprintf(stderr, "granite_bridge: recvfrom: %s\n", std::strerror(errno));
            break;
        }
        buffer[size] = '\0';
        const std::string message(buffer);
        float parsedAngles[kTrackCount];
        const bool validInit = parseInit(message, parsedAngles);
        const bool peerExpired = gBridge.identified &&
                                 gBridge.tick - gBridge.actionTick > kActionTtlTicks;
        if (validInit && (!gBridge.identified || samePeer(source, gBridge.peer) ||
                          peerExpired)) {
            gBridge.peer = source;
            gBridge.peerLen = sourceLen;
            gBridge.identified = true;
            gBridge.actionTick = gBridge.tick;
            gBridge.action = Action();  // never reactivate controls from an older session
            gBridge.advice.clear();
            for (int i = 0; i < kTrackCount; ++i) gBridge.angles[i] = parsedAngles[i];
            configureTrackSensors(car);
            const char reply[] = "***identified***";
            sendto(gBridge.fd, reply, sizeof(reply), 0,
                   (sockaddr *)&gBridge.peer, gBridge.peerLen);
        } else if (gBridge.identified && samePeer(source, gBridge.peer)) {
            parseAction(message, car);
        }
    }
}

template <typename T>
void appendScalar(std::ostringstream &out, const char *tag, T value) {
    out << '(' << tag << ' ' << value << ')';
}

template <typename T>
void appendArray(std::ostringstream &out, const char *tag, const T *values, int count) {
    out << '(' << tag;
    for (int i = 0; i < count; ++i) out << ' ' << values[i];
    out << ')';
}

void sendState(tCarElt *car, tSituation *s) {
    if (gBridge.fd < 0 || !gBridge.identified) return;

    float angle = RtTrackSideTgAngleL(&car->_trkPos) - car->_yaw;
    NORM_PI_PI(angle);
    const float width = RtTrackGetWidth(car->_trkPos.seg, car->_trkPos.toStart);
    const float trackPos = width > 0.0f ? 2.0f * car->_trkPos.toMiddle / width : 0.0f;

    float track[kTrackCount];
    if (trackPos >= -1.0f && trackPos <= 1.0f && gBridge.trackSensors) {
        gBridge.trackSensors->sensors_update();
        for (int i = 0; i < kTrackCount; ++i) track[i] = gBridge.trackSensors->getSensorOut(i);
    } else {
        for (int i = 0; i < kTrackCount; ++i) track[i] = -1.0f;
    }

    float opponents[kOpponentCount];
    if (gBridge.opponentSensors) {
        gBridge.opponentSensors->sensors_update(s);
        for (int i = 0; i < kOpponentCount; ++i)
            opponents[i] = (float)gBridge.opponentSensors->getObstacleSensorOut(i);
    } else {
        for (int i = 0; i < kOpponentCount; ++i) opponents[i] = (float)kRange;
    }

    float wheelSpin[4], tireWear[4], tireTempC[4], tirePressureKPa[4], tireGraining[4];
    for (int i = 0; i < 4; ++i) {
        wheelSpin[i] = car->_wheelSpinVel(i);
        tireWear[i] = car->priv.wheel[i].currentWear;
        tireTempC[i] = car->priv.wheel[i].currentTemperature - 273.15f;
        tirePressureKPa[i] = car->priv.wheel[i].currentPressure / 1000.0f;
        tireGraining[i] = car->priv.wheel[i].currentGraining;
    }
    const float focus[5] = {-1, -1, -1, -1, -1};  // TORCS 1.3.9 has no focus API.

    std::ostringstream out;
    out << std::setprecision(7);
    appendScalar(out, "angle", angle);
    appendScalar(out, "curLapTime", car->_curLapTime);
    appendScalar(out, "damage", car->_dammage);
    appendScalar(out, "distFromStart", car->_distFromStartLine);
    appendScalar(out, "distRaced", car->_distRaced);
    appendScalar(out, "fuel", car->_fuel);
    appendScalar(out, "gear", car->_gear);
    appendScalar(out, "lastLapTime", car->_lastLapTime);
    appendArray(out, "opponents", opponents, kOpponentCount);
    appendScalar(out, "racePos", car->_pos);
    appendScalar(out, "rpm", car->_enginerpm * 10.0);
    appendScalar(out, "speedX", car->_speed_x * 3.6);
    appendScalar(out, "speedY", car->_speed_y * 3.6);
    appendScalar(out, "speedZ", car->_speed_z * 3.6);
    appendArray(out, "track", track, kTrackCount);
    appendScalar(out, "trackPos", trackPos);
    appendArray(out, "wheelSpinVel", wheelSpin, 4);
    appendScalar(out, "z", car->_pos_Z - RtTrackHeightL(&car->_trkPos));
    appendArray(out, "focus", focus, 5);

    // Backward-compatible TORCS 1.3.9 extensions: old clients ignore unknown tags.
    appendArray(out, "tireWear", tireWear, 4);
    appendArray(out, "tireTempC", tireTempC, 4);
    appendArray(out, "tirePressureKPa", tirePressureKPa, 4);
    appendArray(out, "tireGraining", tireGraining, 4);
    // TORCS owns race completion.  Export its counters so the sidecar does not
    // mistake the formation-grid crossing for a completed lap.
    appendScalar(out, "raceLap", car->_laps);
    appendScalar(out, "remainingLaps", car->_remainingLaps);
    appendScalar(out, "totalLaps", s->_totLaps);
    appendScalar(out, "raceFinished", (car->_state & RM_CAR_STATE_FINISH) ? 1 : 0);
    appendScalar(out, "simTime", s->currentTime);

    const std::string message = out.str();
    sendto(gBridge.fd, message.c_str(), message.size() + 1, 0,
           (sockaddr *)&gBridge.peer, gBridge.peerLen);
}

void closeBridge(bool notify) {
    if (gBridge.fd >= 0) {
        if (notify && gBridge.identified) {
            const char message[] = "***shutdown***";
            sendto(gBridge.fd, message, sizeof(message), 0,
                   (sockaddr *)&gBridge.peer, gBridge.peerLen);
        }
        close(gBridge.fd);
        gBridge.fd = -1;
    }
    delete gBridge.trackSensors;
    delete gBridge.opponentSensors;
    gBridge.trackSensors = NULL;
    gBridge.opponentSensors = NULL;
    gBridge.identified = false;
    gBridge.advice.clear();
}

void initTrack(int, tTrack *track, void *, void **carParameters, tSituation *) {
    gTrack = track;
    *carParameters = NULL;
}

void newRace(int index, tCarElt *car, tSituation *s) {
    closeBridge(false);
    gBridge.tick = 0;
    gBridge.actionTick = 0;
    gBridge.action = Action();
    gBridge.advice.clear();
    for (int i = 0; i < kTrackCount; ++i) gBridge.angles[i] = -90.0f + 10.0f * i;
    gExpectedBotId.clear();
    const char *token = std::getenv("GRANITE_BRIDGE_TOKEN");
    if (token != NULL && token[0] != '\0') {
        const std::string candidate(token);
        bool valid = candidate.size() >= 16 && candidate.size() <= 128;
        for (std::string::size_type i = 0; valid && i < candidate.size(); ++i) {
            const unsigned char character = (unsigned char)candidate[i];
            valid = std::isalnum(character) || character == '-' || character == '_';
        }
        if (valid) {
            gExpectedBotId = std::string("SCR:") + candidate;
        } else {
            std::fprintf(stderr, "granite_bridge: invalid GRANITE_BRIDGE_TOKEN; "
                                 "bridge disabled (use 16-128 letters, digits, '-' or '_')\n");
        }
    }
    const bool socketAllowed = token == NULL || token[0] == '\0' || !gExpectedBotId.empty();
    gBridge.fd = socketAllowed ? socket(AF_INET, SOCK_DGRAM, 0) : -1;
    if (gBridge.fd >= 0) {
        const int flags = fcntl(gBridge.fd, F_GETFL, 0);
        const int descriptorFlags = fcntl(gBridge.fd, F_GETFD, 0);
        if (flags < 0 || descriptorFlags < 0 ||
            fcntl(gBridge.fd, F_SETFL, flags | O_NONBLOCK) < 0 ||
            fcntl(gBridge.fd, F_SETFD, descriptorFlags | FD_CLOEXEC) < 0) {
            std::fprintf(stderr, "granite_bridge: cannot make UDP socket non-blocking: %s\n",
                         std::strerror(errno));
            close(gBridge.fd);
            gBridge.fd = -1;
        }
    } else if (socketAllowed) {
        std::fprintf(stderr, "granite_bridge: socket: %s\n", std::strerror(errno));
    }
    if (gBridge.fd >= 0) {
        sockaddr_in server;
        std::memset(&server, 0, sizeof(server));
        server.sin_family = AF_INET;
        server.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        server.sin_port = htons(kPort + index);
        if (bind(gBridge.fd, (sockaddr *)&server, sizeof(server)) < 0) {
            std::fprintf(stderr, "granite_bridge: bind 127.0.0.1:%d: %s\n",
                         kPort + index, std::strerror(errno));
            close(gBridge.fd);
            gBridge.fd = -1;
        }
    }
    configureTrackSensors(car);
    gBridge.opponentSensors = new ObstacleSensors(kOpponentCount, gTrack, car, s, kRange);
}

void displayHud(tCarElt *car, bool fresh) {
    const char *headline = "Apex: WAITING FOR SIDECAR";
    const char *detail = "Start racecoach run";
    float red = 1.0f, green = 0.72f, blue = 0.12f;
    if (fresh && !gBridge.advice.empty()) {
        headline = "Granite 4.1: ADVICE";
        detail = gBridge.advice.c_str();
        red = 0.25f;
        green = 1.0f;
        blue = 0.45f;
    } else if (fresh) {
        headline = "Apex: SIDECAR CONNECTED";
        detail = "No validated advice yet";
        red = 0.25f;
        green = 0.72f;
        blue = 1.0f;
    } else if (gBridge.identified) {
        headline = "Apex: SIDECAR LOST";
        detail = "Full brake failsafe active";
        red = 1.0f;
        green = 0.35f;
        blue = 0.2f;
    }

    std::snprintf(car->_msgCmd[0], sizeof(car->_msgCmd[0]), "%s", headline);
    std::snprintf(car->_msgCmd[1], sizeof(car->_msgCmd[1]), "%s", detail);
    car->_msgColorCmd[0] = red;
    car->_msgColorCmd[1] = green;
    car->_msgColorCmd[2] = blue;
    car->_msgColorCmd[3] = 1.0f;
}

void drive(int, tCarElt *car, tSituation *s) {
    ++gBridge.tick;
    receivePackets(car);
    const bool fresh = gBridge.identified &&
                       gBridge.tick - gBridge.actionTick <= kActionTtlTicks;
    if (!fresh) gBridge.advice.clear();
    if (fresh) {
        car->_accelCmd = gBridge.action.accel;
        car->_brakeCmd = gBridge.action.brake;
        car->_steerCmd = gBridge.action.steer;
        car->_clutchCmd = gBridge.action.clutch;
        car->_gearCmd = gBridge.action.gear;
    } else {
        car->_accelCmd = 0.0f;
        car->_brakeCmd = 1.0f;
        car->_steerCmd = 0.0f;
        car->_clutchCmd = 0.0f;
        car->_gearCmd = car->_gear;
    }
    displayHud(car, fresh);
    sendState(car, s);
}

void endRace(int, tCarElt *, tSituation *) { closeBridge(true); }
void shutdown(int) { closeBridge(true); }

int pitCommand(int, tCarElt *car, tSituation *) {
    car->_pitFuel = 0.0f;
    car->_pitRepair = car->_dammage;
    return ROB_PIT_IM;
}

int initInterface(int index, void *opaque) {
    tRobotItf *interface = (tRobotItf *)opaque;
    interface->rbNewTrack = initTrack;
    interface->rbNewRace = newRace;
    interface->rbEndRace = endRace;
    interface->rbDrive = drive;
    interface->rbPitCmd = pitCommand;
    interface->rbShutdown = shutdown;
    interface->index = index;
    return 0;
}

}  // namespace

extern "C" int granite_bridge(tModInfo *module) {
    std::memset(module, 0, 10 * sizeof(tModInfo));
    module[0].name = ::strdup("Granite Bridge");
    module[0].desc = ::strdup("Non-blocking SCR-compatible IBM Granite bridge");
    module[0].fctInit = initInterface;
    module[0].gfId = ROB_IDENT;
    module[0].index = 0;
    return 0;
}
