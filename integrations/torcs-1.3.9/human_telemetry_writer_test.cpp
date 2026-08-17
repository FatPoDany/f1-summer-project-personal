#include <cassert>
#include <fstream>
#include <string>

#include "apex_human_telemetry_writer.h"

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

    ApexHumanTelemetryWriter writer;
    assert(writer.open(outputDirectory, 1, 1700000000L, 4242L));
    const std::string outputPath = writer.path();

    ApexHumanTelemetrySample sample;
    sample.simTimeS = 12.5;
    sample.deltaTimeS = 0.02;
    sample.carIndex = 0;
    sample.carName = "Human, \"A\"";
    sample.carModel = "car-model";
    sample.driverModule = "human";
    sample.trackName = "Road Track";
    sample.trackInternalName = "road-track";
    sample.raceLap = 2;
    sample.distFromStartM = 123.4;
    sample.accelCmd = 0.7;
    sample.brakeCmd = 0.0;
    sample.steerCmd = -0.1;
    sample.gear = 3;
    sample.totalSpeedMps = 40.0;
    sample.wheels[0].tireTemperatureC = 88.5;
    sample.wheels[1].forceZN = 3210.5;

    assert(writer.write(sample));
    sample.simTimeS = 12.52;
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
    assert(header.find("accel_cmd,brake_cmd,steer_cmd") != std::string::npos);
    assert(header.find("fr_tire_temp_c") != std::string::npos);
    /* Only the vertical load is observable from a driver module, so the
     * unwritable longitudinal/lateral columns must stay out of the schema. */
    assert(header.find("fl_slip_accel_mps,fl_force_z_n,fl_tire_wear") != std::string::npos);
    assert(header.find("_force_x_n") == std::string::npos);
    assert(header.find("_force_y_n") == std::string::npos);
    assert(row1.find("apex-human-v2,0,12.5,0.02") == 0);
    assert(row1.find("\"Human, \"\"A\"\"\"") != std::string::npos);
    assert(row1.find(",3210.5,") != std::string::npos);
    assert(row2.find("apex-human-v2,1,12.52,0.02") == 0);
    assert(csvFieldCount(header) == csvFieldCount(row1));
    assert(csvFieldCount(header) == csvFieldCount(row2));

    assert(writer.open(outputDirectory, 1, 1700000000L, 4242L));
    assert(writer.path() != outputPath);
    assert(writer.write(sample));
    writer.close();

    ApexHumanTelemetryWriter bad;
    assert(!bad.open(outputDirectory + "/missing/subdirectory", 1, 1L, 1L));
    assert(bad.open(outputDirectory, 2, 1700000002L, 4242L));
    bad.close();
    return 0;
}
