# The OpenVINS adaptor: plain ROS 2 Humble on CycloneDDS (TEVV's middleware).
#   docker build -t dhdevspace/auto_mns:openvins-vio-adaptor-0.2.0 components/openvins-vio/adaptor
FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
COPY tevv_adaptor.py estimator_config.yaml kalibr_imu_chain.yaml /adaptor/
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && exec python3 /adaptor/tevv_adaptor.py"]
