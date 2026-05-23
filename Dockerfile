FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

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
    strace \
    sudo \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/kubectl \
    "https://dl.k8s.io/release/v1.28.2/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

WORKDIR /workspace

COPY bin/nodeport-agent /workspace/bin/nodeport-agent
COPY bin/nodeport-syncer /workspace/bin/nodeport-syncer
COPY nodeport_tc.c /workspace/nodeport_tc.c

RUN chmod +x /workspace/bin/nodeport-agent /workspace/bin/nodeport-syncer

CMD ["/bin/bash"]
