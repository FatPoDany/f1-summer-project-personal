#ifndef APEX_HUMAN_TELEMETRY_WRITER_H
#define APEX_HUMAN_TELEMETRY_WRITER_H

#include <fstream>
#include <string>

struct ApexWheelTelemetry {
    double spinVelocityRadS;
    double brakeTemperatureRatio;
    double slipSideMps;
    double slipAccelMps;
    double forceXN;
    double forceYN;
    double forceZN;
    double tireWear;
    double tireTemperatureC;
    double tirePressureKpa;
    double tireGraining;

    ApexWheelTelemetry();
};

struct ApexHumanTelemetrySample {
    double simTimeS;
    double deltaTimeS;
    int carIndex;
    std::string carName;
    std::string carModel;
    std::string driverModule;
    std::string trackName;
    std::string trackInternalName;
    int raceType;
    int raceLap;
    int remainingLaps;
    int racePosition;
    int state;
    int damage;
    int collision;
    int simCollision;
    int collisionCount;
    double currentLapTimeS;
    double lastLapTimeS;
    double bestLapTimeS;
    double distFromStartM;
    double distRacedM;
    double trackToStartM;
    double trackToRightM;
    double trackToMiddleM;
    double trackToLeftM;
    double trackWidthM;
    double normalizedTrackPosition;
    double headingAngleRad;
    int trackSegmentId;
    std::string trackSegmentName;
    int trackSegmentType;
    int trackSegmentType2;
    double trackSegmentLengthM;
    double trackSegmentRadiusM;
    double trackSegmentArcRad;
    std::string trackSurfaceMaterial;
    double trackSurfaceFriction;
    double trackSurfaceRollResistance;
    double trackSurfaceRoughness;
    double trackSurfaceDamage;
    double accelCmd;
    double brakeCmd;
    double steerCmd;
    double clutchCmd;
    int gearCmd;
    int gear;
    double fuelL;
    double engineRpmRadS;
    double engineRpm;
    double positionXM;
    double positionYM;
    double positionZM;
    double rollRad;
    double pitchRad;
    double yawRad;
    double yawRateRadS;
    double speedBodyXMps;
    double speedBodyYMps;
    double speedBodyZMps;
    double speedWorldXMps;
    double speedWorldYMps;
    double speedWorldZMps;
    double totalSpeedMps;
    double accelBodyXMps2;
    double accelBodyYMps2;
    double accelBodyZMps2;
    ApexWheelTelemetry wheels[4];

    ApexHumanTelemetrySample();
};

class ApexHumanTelemetryWriter {
public:
    ApexHumanTelemetryWriter();
    ~ApexHumanTelemetryWriter();

    bool open(const std::string &directory, int driverIndex,
              long timestamp = 0L, long processId = 0L);
    bool write(const ApexHumanTelemetrySample &sample);
    void close();
    const std::string &path() const;
    bool isOpen() const;

private:
    ApexHumanTelemetryWriter(const ApexHumanTelemetryWriter &);
    ApexHumanTelemetryWriter &operator=(const ApexHumanTelemetryWriter &);

    void writeHeader();
    static std::string csvText(const std::string &value);

    std::ofstream output_;
    std::string path_;
    unsigned long openSequence_;
    unsigned long sampleIndex_;
    unsigned int rowsSinceFlush_;
};

#endif
