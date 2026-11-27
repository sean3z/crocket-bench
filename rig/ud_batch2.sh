#!/bin/bash
# cloud-init for every crocket-bench droplet. Self power-off after 3 h as a backstop
# (a powered-off droplet still bills; the box-side doctl watchdog is the real guard).
shutdown -h +240 "crocket-bench 4h cap"
export DEBIAN_FRONTEND=noninteractive
sysctl -w net.core.somaxconn=65535 net.ipv4.tcp_max_syn_backlog=65535 net.ipv4.ip_local_port_range="1024 65000"
ROLE=$(curl -s http://169.254.169.254/metadata/v1/hostname)
case $ROLE in
  cb-srv) apt-get update -q && apt-get install -y -q docker.io docker-buildx jq curl >/var/log/prep.log 2>&1 ;;
  cb-db)  apt-get update -q && apt-get install -y -q docker.io >/var/log/prep.log 2>&1
          docker run -d --name pg --network host -e POSTGRES_USER=bench -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=bench \
            postgres:16 -c max_connections=200 -c shared_buffers=2GB -c synchronous_commit=off -c listen_addresses='*' ;;
  cb-lg)  apt-get update -q && apt-get install -y -q build-essential libssl-dev zlib1g-dev git curl >/var/log/prep.log 2>&1
          git clone -q https://github.com/giltene/wrk2 /opt/wrk2 && cd /opt/wrk2 && make -j8 >>/var/log/prep.log 2>&1 && cp wrk /usr/local/bin/wrk2 ;;
esac
touch /root/ready
