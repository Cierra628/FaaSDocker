# recommend not to use the alpine one, it lacks lots of dependencies
# the slim one ocuppies about 2x space compared to alpine one
# FROM python:3.7-alpine
# FROM docker.io/valian/docker-python-opencv-ffmpeg:py3
FROM python:3.7-slim
# FROM docker.io/jrottenberg/ffmpeg:4.1-alpine

COPY pip.conf /etc/pip.conf

# RUN apt-get -y update && \
#    apt-get -y upgrade

# RUN apt-get -y install gpg

# RUN apt-key adv –keyserver keyserver.ubuntu.com –recv-keys 3B4FE6ACC0B21F32

# RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 40976EAF437D05B5
# RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 3B4FE6ACC0B21F32

# COPY sources.list /etc/apt/sources.list

# RUN apt-get -y update && \
#    apt-get -y upgrade

# RUN apt-get -y install ffmpeg

RUN KEYRING_PATH=/usr/share/keyrings/debian-archive-keyring.gpg && \
    echo "deb [signed-by=${KEYRING_PATH}] http://deb.debian.org/debian bookworm main non-free" > /etc/apt/sources.list.d/non-free.list && \
    echo "deb [signed-by=${KEYRING_PATH}] http://deb.debian.org/debian bookworm-updates main non-free" >> /etc/apt/sources.list.d/non-free.list && \
    echo "deb [signed-by=${KEYRING_PATH}] http://deb.debian.org/debian-security bookworm-security main non-free" >> /etc/apt/sources.list.d/non-free.list

# ----------------------------------------------------------------------
# 🎯 步骤 2: 安装系统依赖
# ----------------------------------------------------------------------
RUN apt-get -y update && \
    apt-get -y install --no-install-recommends \
        # 视频处理
        ffmpeg \
        # 图像和通用库
        libsm6 \
        libxext6 \
        libjpeg-dev \
        zlib1g-dev \
        # 清理
        && rm -rf /var/lib/apt/lists/*

# fulfill the structure requirement of proxy
RUN mkdir /proxy && \
    mkdir /proxy/exec

# copy the proxy server
COPY proxy.py /proxy/
# 假设 actions 目录与 Dockerfile 在同一目录
COPY actions /proxy/exec/actions

# the work dir of proxy is under exec/
WORKDIR /proxy/exec

# proxy server runs under port 5000
EXPOSE 5000

# for alpine base only
# RUN apk update && \
#     apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev make && \
#     pip install --no-cache-dir gevent flask && \
#     apk del .build-deps

RUN pip3 install --no-cache-dir \
    gevent \
    flask \
    boto3 \
    numpy \
    Pillow \
    scikit-learn \
    markdown \
    requests \
    scikit-video \
    couchdb

CMD [ "python3", "/proxy/proxy.py" ]