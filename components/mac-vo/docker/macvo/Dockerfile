# MAC-VO (ICRA 2025, MIT licence) with its ROS 2 wrapper, built from the
# upstream repositories at pinned commits and NOT modified: the component's
# adaptor (../../adaptor) does every bit of fitting it to the platform.
#
#   docker build -t dhdevspace/auto_mns:mac-vo-ros2-a22f89e-cu128 components/mac-vo/docker/macvo
#
# Why these choices:
# - CUDA 12.8 and torch 2.7 cu128: the RTX 50 series is sm_120, which older
#   wheels (MAC-VO's own Dockerfile is CUDA 12.4) have no kernels for.
# - MAC-VO's requirements pin xformers 0.0.27, which is built against torch
#   2.4 and would drag torch back down. No MAC-VO module imports xformers, so
#   it is left out; a constraints file keeps everything else from replacing
#   torch.
# - ROS 2 Humble (Python 3.10) from apt, so the wrapper runs as upstream says:
#   `ros2 run MACVO_ROS2 MACVO --config <file>`.
# - The two model files are downloaded here and checked against their SHA256,
#   so the image is reproducible and starts without network.
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ARG MACVO_ROS2_REF=a22f89edbf4ebedd908a33d0e1032b4db95f53a9
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
ARG FRONTEND_SHA256=bec6edd7e195bab863132f1e9659cdd26e6eaeae7cfd24a626828de294cf5b3a
ARG POSENET_SHA256=32a2456c3a66e46d563f984ea58ceee68c8eaaad525ccda9587e610a33f32bed

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    TORCH_CUDA_ARCH_LIST="12.0" \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

# ROS 2 Humble (ros-base: rclpy, message_filters, sensor/geometry msgs).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg2 lsb-release software-properties-common \
        git wget python3-pip python3-dev ffmpeg libsm6 libxext6 \
    && add-apt-repository -y universe \
    && curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" \
        > /etc/apt/sources.list.d/ros2.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-ros-base ros-humble-message-filters python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade "pip<25" \
    && python3 -m pip install torch==${TORCH_VERSION} torchvision==${TORCHVISION_VERSION} \
        --index-url https://download.pytorch.org/whl/cu128

WORKDIR /opt/ws/src
# MAC-VO's nested submodules (FlowFormer, DPVO) are recorded with SSH URLs;
# the build has no SSH, and they are public, so fetch them over HTTPS.
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" \
    && git clone https://github.com/MAC-VO/MAC-VO-ROS2.git \
    && cd MAC-VO-ROS2 && git checkout ${MACVO_ROS2_REF} \
    && git submodule update --init --recursive

# MAC-VO's requirements, without xformers (see above) and with torch held.
RUN printf 'torch==%s\ntorchvision==%s\n' "${TORCH_VERSION}" "${TORCHVISION_VERSION}" > /tmp/constraints.txt \
    && grep -viE '^\s*(xformers|wandb)' MAC-VO-ROS2/MACVO_ROS2/src/requirements.txt > /tmp/requirements.txt \
    && python3 -m pip install -r /tmp/requirements.txt -c /tmp/constraints.txt

# The wrapper installs Model/* into its share directory, so the weights go
# there before colcon builds it.
RUN mkdir -p MAC-VO-ROS2/Model \
    && wget -q -O MAC-VO-ROS2/Model/MACVO_FrontendCov.pth \
        https://github.com/MAC-VO/MAC-VO/releases/download/model/MACVO_FrontendCov.pth \
    && wget -q -O MAC-VO-ROS2/Model/MACVO_posenet.pkl \
        https://github.com/MAC-VO/MAC-VO/releases/download/model/MACVO_posenet.pkl \
    && echo "${FRONTEND_SHA256}  MAC-VO-ROS2/Model/MACVO_FrontendCov.pth" | sha256sum -c - \
    && echo "${POSENET_SHA256}  MAC-VO-ROS2/Model/MACVO_posenet.pkl" | sha256sum -c -

WORKDIR /opt/ws
RUN . /opt/ros/humble/setup.sh && colcon build --symlink-install

COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "run", "MACVO_ROS2", "MACVO", "--config", "/work/macvo.yaml"]

LABEL org.opencontainers.image.title="mac-vo-ros2" \
      org.opencontainers.image.licenses="MIT" \
      tevv.upstream.macvo_ros2="https://github.com/MAC-VO/MAC-VO-ROS2@${MACVO_ROS2_REF}" \
      tevv.model.frontend_sha256="${FRONTEND_SHA256}" \
      tevv.model.posenet_sha256="${POSENET_SHA256}" \
      tevv.torch="${TORCH_VERSION}+cu128"
