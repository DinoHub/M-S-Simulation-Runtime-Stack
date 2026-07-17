"""Default AirSim sensor and camera payloads used by generated scenarios."""
from __future__ import annotations

DEFAULT_BAROMETER = {
    "SensorType": 1,
    "Enabled": True,
    "PressureFactorSigma": 0.0001825,
}

DEFAULT_GPS = {
    "SensorType": 3,
    "Enabled": True,
    "EphTimeConstant": 0.3,
    "EpvTimeConstant": 0.3,
    "EphInitial": 2.0,
    "EpvInitial": 3.0,
    "EphFinal": 0.1,
    "EpvFinal": 0.1,
    "EphMin3d": 3.0,
    "EphMin2d": 4.0,
    "UpdateLatency": 0.1,
    "UpdateFrequency": 50,
    "StartupDelay": 0,
}

DEFAULT_LIDAR = {
    "SensorType": 6,
    "Enabled": False,
    "ExternalController": False,
    "NumberOfChannels": 16,
    "RotationsPerSecond": 30,
    "PointsPerSecond": 200000,
    "X": 0,
    "Y": 0,
    "Z": 0.5,
    "Roll": 0,
    "Pitch": 0,
    "Yaw": 0,
    "VerticalFOVUpper": 15,
    "VerticalFOVLower": -25,
    "HorizontalFOVStart": -180,
    "HorizontalFOVEnd": 180,
    "DrawDebugPoints": False,
}

DEFAULT_IMU = {"SensorType": 2, "Enabled": True}
DEFAULT_MAGNETOMETER = {"SensorType": 4, "Enabled": True}
DEFAULT_DISTANCE = {
    "SensorType": 5,
    "Enabled": False,
    "X": 0,
    "Y": 0,
    "Z": 0,
    "Pitch": 0,
    "Roll": 0,
    "Yaw": 0,
    "MinRange": 0.2,
    "MaxRange": 40,
    "DrawDebugPoints": False,
}
DEFAULT_ECHO = {"SensorType": 7, "Enabled": False}
DEFAULT_GPU_LIDAR = {
    **DEFAULT_LIDAR,
    "SensorType": 8,
}
DEFAULT_SENSOR_TEMPLATE = {"SensorType": 9, "Enabled": False}
DEFAULT_MARLOC_UWB = {"SensorType": 10, "Enabled": False}
DEFAULT_WIFI = {"SensorType": 11, "Enabled": False}

DEFAULT_SENSOR_BLOCK = {
    "Barometer": DEFAULT_BAROMETER,
    "Gps": DEFAULT_GPS,
    "LidarSensor1": DEFAULT_LIDAR,
}

STANDARD_SENSOR_DEFAULTS = {
    "barometer": ("Barometer", DEFAULT_BAROMETER),
    "gps": ("Gps", DEFAULT_GPS),
    "imu": ("Imu", DEFAULT_IMU),
    "magnetometer": ("Magnetometer", DEFAULT_MAGNETOMETER),
    "distance": ("DistanceSensor1", DEFAULT_DISTANCE),
    "echo": ("EchoSensor1", DEFAULT_ECHO),
    "gpu_lidar": ("GPULidarSensor1", DEFAULT_GPU_LIDAR),
    "sensor_template": ("SensorTemplate1", DEFAULT_SENSOR_TEMPLATE),
    "marloc_uwb": ("MarlocUwb1", DEFAULT_MARLOC_UWB),
    "wifi": ("Wifi1", DEFAULT_WIFI),
}
