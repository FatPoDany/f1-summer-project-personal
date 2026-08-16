#ifndef APEX_ROBOT_TELEMETRY_H
#define APEX_ROBOT_TELEMETRY_H

#include <car.h>
#include <raceman.h>
#include <track.h>

void ApexRobotTelemetryStart(int index, tCarElt *car, tSituation *s, tTrack *track);
void ApexRobotTelemetryRecord(int index, tCarElt *car, tSituation *s, tTrack *track);
void ApexRobotTelemetryStop(int index);

#endif
