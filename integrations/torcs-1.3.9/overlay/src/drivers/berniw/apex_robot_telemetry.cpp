#include "apex_robot_telemetry.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>

#include <robottools.h>

#include "apex_robot_telemetry_writer.h"

namespace {

const int kReferenceRobotIndex = 9;
const double kRadiansPerSecondToRpm = 60.0 / (2.0 * 3.14159265358979323846);
ApexRobotTelemetryWriter gWriter;
bool gWriteErrorReported = false;

bool validIndex(int index) { return index == kReferenceRobotIndex; }

std::string textOrEmpty(const char *value) {
    return value == NULL ? std::string() : std::string(value);
}

double distanceAlongSegment(const tTrkLocPos &position) {
    if (position.seg == NULL) return 0.0;
    if (position.seg->type == TR_STR) return position.toStart;
    return position.toStart * position.seg->radius;
}

void fillTrack(ApexRobotTelemetrySample *sample, tCarElt *car, tTrack *track) {
    tTrkLocPos &position = car->_trkPos;
    tTrackSeg *segment = position.seg;
    sample->trackName = track == NULL ? "" : textOrEmpty(track->name);
    sample->trackInternalName = track == NULL ? "" : textOrEmpty(track->internalname);
    sample->trackToStartM = distanceAlongSegment(position);
    sample->trackToRightM = position.toRight;
    sample->trackToMiddleM = position.toMiddle;
    sample->trackToLeftM = position.toLeft;
    if (segment == NULL) return;

    sample->trackWidthM = RtTrackGetWidth(segment, position.toStart);
    if (sample->trackWidthM > 0.0)
        sample->normalizedTrackPosition = 2.0 * position.toMiddle / sample->trackWidthM;
    sample->headingAngleRad = RtTrackSideTgAngleL(&position) - car->_yaw;
    NORM_PI_PI(sample->headingAngleRad);
    sample->trackSegmentId = segment->id;
    sample->trackSegmentName = textOrEmpty(segment->name);
    sample->trackSegmentType = segment->type;
    sample->trackSegmentType2 = segment->type2;
    sample->trackSegmentLengthM = segment->length;
    sample->trackSegmentRadiusM = segment->radius;
    sample->trackSegmentArcRad = segment->arc;
    if (segment->surface != NULL) {
        sample->trackSurfaceMaterial = textOrEmpty(segment->surface->material);
        sample->trackSurfaceFriction = segment->surface->kFriction;
        sample->trackSurfaceRollResistance = segment->surface->kRollRes;
        sample->trackSurfaceRoughness = segment->surface->kRoughness;
        sample->trackSurfaceDamage = segment->surface->kDammage;
    }
}

void fillWheels(ApexRobotTelemetrySample *sample, tCarElt *car) {
    for (int wheel = 0; wheel < 4; ++wheel) {
        ApexRobotWheelTelemetry &target = sample->wheels[wheel];
        const tWheelState &source = car->priv.wheel[wheel];
        target.spinVelocityRadS = source.spinVel;
        target.brakeTemperatureRatio = source.brakeTemp;
        target.slipSideMps = source.slipSide;
        target.slipAccelMps = source.slipAccel;
        target.forceXN = source.Fx;
        target.forceYN = source.Fy;
        target.forceZN = source.Fz;
        target.tireWear = source.currentWear;
        target.tireTemperatureC = source.currentTemperature - 273.15;
        target.tirePressureKpa = source.currentPressure / 1000.0;
        target.tireGraining = source.currentGraining;
    }
}

}  // namespace

void ApexRobotTelemetryStart(int index, tCarElt *, tSituation *, tTrack *) {
    if (!validIndex(index)) return;
    ApexRobotTelemetryStop(index);
    gWriteErrorReported = false;
    const char *directory = std::getenv("APEX_SYNTHETIC_TELEMETRY_DIR");
    if (directory == NULL || directory[0] == '\0') return;
    try {
        if (!gWriter.open(directory, index)) {
            std::fprintf(stderr, "apex robot telemetry: cannot write to %s\n", directory);
            gWriteErrorReported = true;
        }
    } catch (...) {
        gWriter.close();
        std::fprintf(stderr, "apex robot telemetry: recorder initialization failed safely\n");
        gWriteErrorReported = true;
    }
}

void ApexRobotTelemetryRecord(int index, tCarElt *car, tSituation *s, tTrack *track) {
    if (!validIndex(index) || car == NULL || s == NULL || s->currentTime < 0.0 ||
        !gWriter.isOpen())
        return;
    try {
        ApexRobotTelemetrySample sample;
        sample.simTimeS = s->currentTime;
        sample.deltaTimeS = s->deltaTime;
        sample.carIndex = car->index;
        sample.driverIndex = index;
        sample.carName = textOrEmpty(car->_name);
        sample.carModel = textOrEmpty(car->_carName);
        sample.driverModule = textOrEmpty(car->_modName);
        sample.raceType = s->_raceType;
        sample.raceLap = car->_laps;
        sample.remainingLaps = car->_remainingLaps;
        sample.racePosition = car->_pos;
        sample.state = car->_state;
        sample.raceFinished = (car->_state & RM_CAR_STATE_FINISH) != 0 ? 1 : 0;
        sample.damage = car->_dammage;
        sample.collision = car->priv.collision;
        sample.simCollision = car->priv.simcollision;
        sample.collisionCount = car->priv.collision_state.collision_count;
        sample.currentLapTimeS = car->_curLapTime;
        sample.lastLapTimeS = car->_lastLapTime;
        sample.bestLapTimeS = car->_bestLapTime;
        sample.distFromStartM = car->_distFromStartLine;
        sample.distRacedM = car->_distRaced;
        sample.accelCmd = car->_accelCmd;
        sample.brakeCmd = car->_brakeCmd;
        sample.steerCmd = car->_steerCmd;
        sample.clutchCmd = car->_clutchCmd;
        sample.gearCmd = car->_gearCmd;
        sample.gear = car->_gear;
        sample.fuelL = car->_fuel;
        sample.engineRpmRadS = car->_enginerpm;
        sample.engineRpm = car->_enginerpm * kRadiansPerSecondToRpm;
        sample.positionXM = car->_pos_X;
        sample.positionYM = car->_pos_Y;
        sample.positionZM = car->_pos_Z;
        sample.rollRad = car->_roll;
        sample.pitchRad = car->_pitch;
        sample.yawRad = car->_yaw;
        sample.yawRateRadS = car->_yaw_rate;
        sample.speedBodyXMps = car->_speed_x;
        sample.speedBodyYMps = car->_speed_y;
        sample.speedBodyZMps = car->_speed_z;
        sample.speedWorldXMps = car->_speed_X;
        sample.speedWorldYMps = car->_speed_Y;
        sample.speedWorldZMps = car->pub.DynGCg.vel.z;
        sample.totalSpeedMps = car->pub.speed;
        sample.accelBodyXMps2 = car->_accel_x;
        sample.accelBodyYMps2 = car->_accel_y;
        sample.accelBodyZMps2 = car->_accel_z;
        fillTrack(&sample, car, track);
        fillWheels(&sample, car);
        if (!gWriter.write(sample) && !gWriteErrorReported) {
            std::fprintf(stderr, "apex robot telemetry: write failed; disabled safely\n");
            gWriteErrorReported = true;
            gWriter.close();
        }
    } catch (...) {
        if (!gWriteErrorReported)
            std::fprintf(stderr, "apex robot telemetry: sample failed; disabled safely\n");
        gWriteErrorReported = true;
        gWriter.close();
    }
}

void ApexRobotTelemetryStop(int index) {
    if (!validIndex(index)) return;
    try {
        gWriter.close();
    } catch (...) {
        std::fprintf(stderr, "apex robot telemetry: close failed safely\n");
    }
}
