FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# Retry package downloads on slow or flaky connections
RUN npm config set fetch-retries 6 && npm config set fetch-retry-maxtimeout 120000 && npm config set fetch-timeout 600000 && \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
