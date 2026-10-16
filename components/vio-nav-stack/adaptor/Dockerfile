# The VIO navigation stack's adaptor: ROS 2 Humble on CycloneDDS (TEVV's
# middleware), with mavros_msgs for SUPER's PositionTarget.
#   docker build -t dhdevspace/auto_mns:vio-nav-stack-adaptor-0.1.0 components/vio-nav-stack/adaptor
FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
        ros-humble-mavros-msgs \
    && rm -rf /var/lib/apt/lists/*
COPY tevv_adaptor.py estimator_config.yaml kalibr_imu_chain.yaml /adaptor/
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && exec python3 /adaptor/tevv_adaptor.py"]
