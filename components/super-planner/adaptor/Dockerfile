# SUPER's TEVV adaptor (from `component init --kind planner`): ROS 2 Humble on CycloneDDS,
# with mavros_msgs for SUPER's PositionTarget output.
#   docker build -t dhdevspace/auto_mns:super-planner-adaptor-0.2.0 components/super-planner/adaptor
FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
        ros-humble-mavros-msgs \
    && rm -rf /var/lib/apt/lists/*
COPY tevv_adaptor.py /adaptor/
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && exec python3 /adaptor/tevv_adaptor.py"]
