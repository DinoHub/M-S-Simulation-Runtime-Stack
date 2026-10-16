# sample-goto: a ROS 2 Humble node on CycloneDDS (TEVV's middleware).
#   docker build -t dhdevspace/auto_mns:sample-goto-0.1.0 components/sample-goto/image
FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
COPY goto.py /opt/goto.py
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && exec python3 /opt/goto.py"]
