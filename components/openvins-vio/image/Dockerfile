# TEVV runs every container on CycloneDDS; this image adds it to the
# OpenVINS image the demo was recorded with. Nothing else changes.
#   docker build -t dhdevspace/auto_mns:vio-estimator-openvins-69488123-cyclone components/openvins-vio/image
FROM dhdevspace/auto_mns:vio-estimator-openvins-69488123
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
