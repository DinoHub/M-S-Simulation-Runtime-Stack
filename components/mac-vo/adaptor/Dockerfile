# The MAC-VO adaptor: plain ROS 2 Humble and numpy, no GPU. See
# ../component.yaml and ../README.md.
FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends python3-numpy python3-yaml \
    && rm -rf /var/lib/apt/lists/*
COPY mns_macvo_adaptor /opt/mns_macvo_adaptor/mns_macvo_adaptor
ENV PYTHONPATH=/opt/mns_macvo_adaptor
RUN printf '#!/bin/bash\nsource /opt/ros/humble/setup.bash\nexec python3 -m mns_macvo_adaptor "$@"\n' \
        > /usr/local/bin/mns-macvo-adaptor && chmod 0755 /usr/local/bin/mns-macvo-adaptor
ENTRYPOINT ["/usr/local/bin/mns-macvo-adaptor"]
CMD ["run"]
LABEL org.opencontainers.image.title="mac-vo-adaptor"
