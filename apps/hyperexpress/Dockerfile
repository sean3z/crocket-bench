# glibc (Debian trixie, glibc 2.41) base: uWebSockets.js ships prebuilt glibc .node binaries; bookworm (2.36) is too old.
FROM node:24-trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm ls --depth=0 && node -e "require('uWebSockets.js')"
COPY server.js ./
ENV NODE_ENV=production POOL_SIZE=16
EXPOSE 8000
ENTRYPOINT ["node", "server.js"]
