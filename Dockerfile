FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    bash \
    bpftool \
    ca-certificates \
    clang \
    curl \
    iproute2 \
    iputils-ping \
    libc6-dev \
    libbpf-dev \
    netcat-openbsd \
    python3 \
    strace \
    sudo \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/kubectl \
    "https://dl.k8s.io/release/v1.28.2/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

WORKDIR /workspace

COPY nodeport_tc.c /workspace/nodeport_tc.c
COPY scripts /workspace/scripts

RUN chmod +x /workspace/scripts/*.sh /workspace/scripts/*.py

CMD ["/bin/bash"]
