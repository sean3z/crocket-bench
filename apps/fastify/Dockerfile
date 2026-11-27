FROM node:26-slim
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm ls --depth=0
COPY server.js ./
ENV NODE_ENV=production POOL_SIZE=16
EXPOSE 8000
ENTRYPOINT ["node", "server.js"]
