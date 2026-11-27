# Drogon v1.9.14 from source (Postgres only), Release; app built with GCC 13 (C++20).
FROM ubuntu:24.04 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates cmake g++ make \
      libjsoncpp-dev uuid-dev zlib1g-dev libssl-dev libpq-dev libbrotli-dev libc-ares-dev && rm -rf /var/lib/apt/lists/*
RUN git clone -q --depth 1 --branch v1.9.14 --recurse-submodules https://github.com/drogonframework/drogon /drogon \
 && cmake -S /drogon -B /drogon/build -DCMAKE_BUILD_TYPE=Release -DBUILD_EXAMPLES=OFF -DBUILD_CTL=OFF -DBUILD_ORM=ON \
      -DBUILD_POSTGRESQL=ON -DBUILD_MYSQL=OFF -DBUILD_SQLITE=OFF -DBUILD_REDIS=OFF -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF \
 && cmake --build /drogon/build -j4 && cmake --install /drogon/build
WORKDIR /src
COPY CMakeLists.txt main.cc ./
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends libjsoncpp25 libuuid1 zlib1g libssl3t64 libpq5 libbrotli1 libc-ares2 curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bench /app/bench
EXPOSE 8000
ENTRYPOINT ["/app/bench"]
