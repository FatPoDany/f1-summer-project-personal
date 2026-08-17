#include "apex_robot_telemetry_writer.h"

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

#include <ctime>
#include <iomanip>
#include <sstream>

namespace {

const unsigned int kFlushRows = 50;
const int kReferenceRobotIndex = 9;
const char *kSchemaVersion = "apex-robot-v2";
const char *kWheelNames[4] = {"fr", "fl", "rr", "rl"};

/* The filename needs one value that differs per concurrent simulator, and
 * MSVC spells the POSIX call with a leading underscore. */
long currentProcessId() {
#ifdef _WIN32
    return static_cast<long>(_getpid());
#else
    return static_cast<long>(getpid());
#endif
}

}  // namespace

ApexRobotWheelTelemetry::ApexRobotWheelTelemetry()
    : spinVelocityRadS(0.0), brakeTemperatureRatio(0.0), slipSideMps(0.0),
      slipAccelMps(0.0), forceZN(0.0), tireWear(0.0),
      tireTemperatureC(0.0), tirePressureKpa(0.0), tireGraining(0.0) {}

ApexRobotTelemetrySample::ApexRobotTelemetrySample()
    : simTimeS(0.0), deltaTimeS(0.0), carIndex(0), driverIndex(0), raceType(0), raceLap(0),
      remainingLaps(0), racePosition(0), state(0), raceFinished(0), damage(0),
      collision(0), simCollision(0), collisionCount(0), currentLapTimeS(0.0),
      lastLapTimeS(0.0), bestLapTimeS(0.0), distFromStartM(0.0), distRacedM(0.0),
      trackToStartM(0.0), trackToRightM(0.0), trackToMiddleM(0.0), trackToLeftM(0.0),
      trackWidthM(0.0), normalizedTrackPosition(0.0), headingAngleRad(0.0),
      trackSegmentId(0), trackSegmentType(0), trackSegmentType2(0),
      trackSegmentLengthM(0.0), trackSegmentRadiusM(0.0), trackSegmentArcRad(0.0),
      trackSurfaceFriction(0.0), trackSurfaceRollResistance(0.0),
      trackSurfaceRoughness(0.0), trackSurfaceDamage(0.0), accelCmd(0.0),
      brakeCmd(0.0), steerCmd(0.0), clutchCmd(0.0), gearCmd(0), gear(0), fuelL(0.0),
      engineRpmRadS(0.0), engineRpm(0.0), positionXM(0.0), positionYM(0.0),
      positionZM(0.0), rollRad(0.0), pitchRad(0.0), yawRad(0.0), yawRateRadS(0.0),
      speedBodyXMps(0.0), speedBodyYMps(0.0), speedBodyZMps(0.0),
      speedWorldXMps(0.0), speedWorldYMps(0.0), speedWorldZMps(0.0),
      totalSpeedMps(0.0), accelBodyXMps2(0.0), accelBodyYMps2(0.0),
      accelBodyZMps2(0.0) {}

ApexRobotTelemetryWriter::ApexRobotTelemetryWriter()
    : openSequence_(0), sampleIndex_(0), rowsSinceFlush_(0) {}

ApexRobotTelemetryWriter::~ApexRobotTelemetryWriter() { close(); }

bool ApexRobotTelemetryWriter::open(const std::string &directory, int driverIndex,
                                    long timestamp, long processId) {
    close();
    if (directory.empty() || driverIndex != kReferenceRobotIndex) return false;
    if (timestamp == 0L) timestamp = static_cast<long>(std::time(NULL));
    if (processId == 0L) processId = currentProcessId();
    output_.clear();
    ++openSequence_;

    std::ostringstream filename;
    filename << "robot-berniw9-" << timestamp << '-' << processId << '-'
             << openSequence_ << ".csv";
    path_ = directory;
    if (path_[path_.size() - 1] != '/') path_ += '/';
    path_ += filename.str();

    output_.open(path_.c_str(), std::ios::out | std::ios::trunc);
    if (!output_.good()) {
        output_.close();
        path_.clear();
        return false;
    }
    sampleIndex_ = 0;
    rowsSinceFlush_ = 0;
    output_ << std::setprecision(10);
    writeHeader();
    output_.flush();
    return output_.good();
}

bool ApexRobotTelemetryWriter::write(const ApexRobotTelemetrySample &sample) {
    if (!output_.is_open()) return false;
    output_ << kSchemaVersion << ',' << sampleIndex_++ << ',' << sample.simTimeS << ','
            << sample.deltaTimeS << ',' << sample.carIndex << ',' << sample.driverIndex << ','
            << csvText(sample.carName)
            << ',' << csvText(sample.carModel) << ',' << csvText(sample.driverModule) << ','
            << csvText(sample.trackName) << ',' << csvText(sample.trackInternalName) << ','
            << sample.raceType << ',' << sample.raceLap << ',' << sample.remainingLaps << ','
            << sample.racePosition << ',' << sample.state << ',' << sample.raceFinished << ','
            << sample.damage << ',' << sample.collision << ',' << sample.simCollision << ','
            << sample.collisionCount << ',' << sample.currentLapTimeS << ','
            << sample.lastLapTimeS << ',' << sample.bestLapTimeS << ','
            << sample.distFromStartM << ',' << sample.distRacedM << ','
            << sample.trackToStartM << ',' << sample.trackToRightM << ','
            << sample.trackToMiddleM << ',' << sample.trackToLeftM << ','
            << sample.trackWidthM << ',' << sample.normalizedTrackPosition << ','
            << sample.headingAngleRad << ',' << sample.trackSegmentId << ','
            << csvText(sample.trackSegmentName) << ',' << sample.trackSegmentType << ','
            << sample.trackSegmentType2 << ',' << sample.trackSegmentLengthM << ','
            << sample.trackSegmentRadiusM << ',' << sample.trackSegmentArcRad << ','
            << csvText(sample.trackSurfaceMaterial) << ',' << sample.trackSurfaceFriction << ','
            << sample.trackSurfaceRollResistance << ',' << sample.trackSurfaceRoughness << ','
            << sample.trackSurfaceDamage << ',' << sample.accelCmd << ',' << sample.brakeCmd
            << ',' << sample.steerCmd << ',' << sample.clutchCmd << ',' << sample.gearCmd
            << ',' << sample.gear << ',' << sample.fuelL << ',' << sample.engineRpmRadS << ','
            << sample.engineRpm << ',' << sample.positionXM << ',' << sample.positionYM << ','
            << sample.positionZM << ',' << sample.rollRad << ',' << sample.pitchRad << ','
            << sample.yawRad << ',' << sample.yawRateRadS << ',' << sample.speedBodyXMps << ','
            << sample.speedBodyYMps << ',' << sample.speedBodyZMps << ','
            << sample.speedWorldXMps << ',' << sample.speedWorldYMps << ','
            << sample.speedWorldZMps << ',' << sample.totalSpeedMps << ','
            << sample.accelBodyXMps2 << ',' << sample.accelBodyYMps2 << ','
            << sample.accelBodyZMps2;
    for (int wheel = 0; wheel < 4; ++wheel) {
        const ApexRobotWheelTelemetry &value = sample.wheels[wheel];
        output_ << ',' << value.spinVelocityRadS << ',' << value.brakeTemperatureRatio << ','
                << value.slipSideMps << ',' << value.slipAccelMps
                << ',' << value.forceZN << ',' << value.tireWear
                << ',' << value.tireTemperatureC << ',' << value.tirePressureKpa << ','
                << value.tireGraining;
    }
    output_ << '\n';
    ++rowsSinceFlush_;
    if (rowsSinceFlush_ >= kFlushRows) {
        output_.flush();
        rowsSinceFlush_ = 0;
    }
    return output_.good();
}

void ApexRobotTelemetryWriter::close() {
    if (output_.is_open()) {
        output_.flush();
        output_.close();
    }
    rowsSinceFlush_ = 0;
}

const std::string &ApexRobotTelemetryWriter::path() const { return path_; }

bool ApexRobotTelemetryWriter::isOpen() const { return output_.is_open(); }

void ApexRobotTelemetryWriter::writeHeader() {
    output_ << "schema_version,sample,sim_time_s,delta_time_s,car_index,driver_index,car_name,"
               "car_model,driver_module,track_name,track_internal_name,race_type,race_lap,"
               "remaining_laps,race_pos,state,race_finished,damage,collision,simcollision,"
               "collision_count,cur_lap_time_s,last_lap_time_s,best_lap_time_s,"
               "dist_from_start_m,dist_raced_m,track_to_start_m,track_to_right_m,"
               "track_to_middle_m,track_to_left_m,track_width_m,track_pos,angle_rad,"
               "track_seg_id,track_seg_name,track_seg_type,track_seg_type2,"
               "track_seg_length_m,track_seg_radius_m,track_seg_arc_rad,"
               "track_surface_material,track_surface_friction,track_surface_roll_resistance,"
               "track_surface_roughness,track_surface_damage,accel_cmd,brake_cmd,steer_cmd,"
               "clutch_cmd,gear_cmd,gear,fuel_l,engine_rpm_rad_s,engine_rpm,pos_x_m,pos_y_m,"
               "pos_z_m,roll_rad,pitch_rad,yaw_rad,yaw_rate_rad_s,speed_body_x_mps,"
               "speed_body_y_mps,speed_body_z_mps,speed_world_x_mps,speed_world_y_mps,"
               "speed_world_z_mps,total_speed_mps,accel_body_x_mps2,accel_body_y_mps2,"
               "accel_body_z_mps2";
    for (int wheel = 0; wheel < 4; ++wheel) {
        output_ << ',' << kWheelNames[wheel] << "_spin_vel_rad_s," << kWheelNames[wheel]
                << "_brake_temp_ratio," << kWheelNames[wheel] << "_slip_side_mps,"
                << kWheelNames[wheel] << "_slip_accel_mps,"
                << kWheelNames[wheel] << "_force_z_n," << kWheelNames[wheel]
                << "_tire_wear," << kWheelNames[wheel] << "_tire_temp_c,"
                << kWheelNames[wheel] << "_tire_pressure_kpa," << kWheelNames[wheel]
                << "_tire_graining";
    }
    output_ << '\n';
}

std::string ApexRobotTelemetryWriter::csvText(const std::string &value) {
    if (value.find_first_of(",\"\r\n") == std::string::npos) return value;
    std::string escaped("\"");
    for (std::string::size_type i = 0; i < value.size(); ++i) {
        if (value[i] == '\"') escaped += '\"';
        escaped += value[i];
    }
    escaped += '\"';
    return escaped;
}
