#include <cassert>
#include <fstream>
#include <string>

#include "apex_robot_telemetry_writer.h"

static unsigned int csvFieldCount(const std::string &line) {
    bool quoted = false;
    unsigned int fields = 1;
    for (std::string::size_type i = 0; i < line.size(); ++i) {
        if (line[i] == '"') {
            if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
                ++i;
            } else {
                quoted = !quoted;
            }
        } else if (line[i] == ',' && !quoted) {
            ++fields;
        }
    }
    assert(!quoted);
    return fields;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const std::string outputDirectory(argv[1]);

    ApexRobotTelemetryWriter writer;
    assert(!writer.open(outputDirectory, 8, 1700000000L, 4242L));
    assert(writer.open(outputDirectory, 9, 1700000000L, 4242L));
    const std::string outputPath = writer.path();

    ApexRobotTelemetrySample sample;
    sample.simTimeS = 12.5;
    sample.deltaTimeS = 0.02;
    sample.carIndex = 0;
    sample.driverIndex = 9;
    sample.carName = "berniw, \"9\"";
    sample.carModel = "car7-trb1";
    sample.driverModule = "berniw";
    sample.trackName = "Grand Prix Track 1";
    sample.trackInternalName = "g-track-1";
    sample.raceLap = 2;
    sample.remainingLaps = 2;
    sample.distFromStartM = 123.4;
    sample.accelCmd = 0.7;
    sample.brakeCmd = 0.0;
    sample.steerCmd = -0.1;
    sample.gear = 3;
    sample.totalSpeedMps = 40.0;
    sample.raceFinished = 0;
    sample.wheels[0].tireTemperatureC = 88.5;

    assert(writer.write(sample));
    sample.simTimeS = 12.52;
    sample.raceFinished = 1;
    assert(writer.write(sample));
    writer.close();

    std::ifstream input(outputPath.c_str());
    assert(input.good());
    std::string header;
    std::string row1;
    std::string row2;
    std::getline(input, header);
    std::getline(input, row1);
    std::getline(input, row2);
    assert(header.find("schema_version,sample,sim_time_s,delta_time_s") == 0);
    assert(header.find("dist_from_start_m") != std::string::npos);
    assert(header.find("car_index,driver_index,car_name") != std::string::npos);
    assert(header.find("accel_cmd,brake_cmd,steer_cmd") != std::string::npos);
    assert(header.find("fr_tire_temp_c") != std::string::npos);
    assert(header.find("race_finished") != std::string::npos);
    assert(row1.find("apex-robot-v1,0,12.5,0.02") == 0);
    assert(row1.find("\"berniw, \"\"9\"\"\"") != std::string::npos);
    assert(row2.find("apex-robot-v1,1,12.52,0.02") == 0);
    assert(csvFieldCount(header) == csvFieldCount(row1));
    assert(csvFieldCount(header) == csvFieldCount(row2));

    assert(writer.open(outputDirectory, 9, 1700000000L, 4242L));
    assert(writer.path() != outputPath);
    assert(writer.write(sample));
    writer.close();

    ApexRobotTelemetryWriter bad;
    assert(!bad.open(outputDirectory + "/missing/subdirectory", 9, 1L, 1L));
    return 0;
}
