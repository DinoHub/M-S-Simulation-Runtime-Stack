# SUPER as recorded in the demos (super_runtime-ms.1) with CycloneDDS, TEVV's middleware.
#   docker build -t dhdevspace/auto_mns:super_runtime-ms.1-cyclone components/super-planner/image
FROM dhdevspace/auto_mns:super_runtime-ms.1
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-rmw-cyclonedds-cpp \
    && rm -rf /var/lib/apt/lists/*
