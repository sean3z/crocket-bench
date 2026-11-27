# crocket 0.5.2 (FetchContent tag), GCC 16.2, Release. Runs in the toolchain image so the
# GCC 16.2 runtime matches the build exactly.
#
# The three marked WORKAROUNDs paper over gaps in ghcr.io/sean3z/crocket-toolchain:gcc-16.2;
# a fix to the toolchain image is in progress, after which they can go.
FROM ghcr.io/sean3z/crocket-toolchain:gcc-16.2
# WORKAROUND 1: the image has no libpqxx-dev (CROCKET_PG=ON fails with "libpqxx was not found")
# and no dev packages for h2o's optional deps (zstd, brotli, libcap). Uses Ubuntu 26.04's libpqxx.
RUN apt-get update && apt-get install -y --no-install-recommends libpqxx-dev libpq-dev pkg-config libzstd-dev libbrotli-dev libcap-dev zlib1g-dev libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY CMakeLists.txt main.cpp ./
# WORKAROUND 2: CMAKE_C_COMPILER must be set too: crocket's project() enables C (for h2o) and the
# image has no default `cc`, so configure fails with "No CMAKE_C_COMPILER could be found".
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=g++-16.2 -DCMAKE_C_COMPILER=gcc-16.2 && cmake --build build --target bench && cp build/bench /usr/local/bin/bench
# WORKAROUND 3: the binary needs GCC 16.2's libstdc++ (GLIBCXX_3.4.36); the system one is 3.4.35.
ENV LD_LIBRARY_PATH=/opt/gcc-16.2/lib64
ENV CROCKET_PORT=8000 CROCKET_DATABASE_POOL_SIZE=16 CROCKET_REQUEST_TIMEOUT=10s
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/bench"]
