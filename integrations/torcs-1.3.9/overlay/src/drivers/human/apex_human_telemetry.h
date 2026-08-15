#ifndef APEX_HUMAN_TELEMETRY_H
#define APEX_HUMAN_TELEMETRY_H

#include <car.h>
#include <raceman.h>
#include <track.h>

void ApexHumanTelemetryStart(int index, tCarElt *car, tSituation *s, tTrack *track);
void ApexHumanTelemetryRecord(int index, tCarElt *car, tSituation *s, tTrack *track);
void ApexHumanTelemetryStop(int index);

#endif
