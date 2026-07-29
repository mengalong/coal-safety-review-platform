ARG NODE_BASE_IMAGE=public.ecr.aws/docker/library/node:22-alpine
ARG NGINX_BASE_IMAGE=public.ecr.aws/nginx/nginx-unprivileged:1.27-alpine
FROM ${NODE_BASE_IMAGE} AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM ${NGINX_BASE_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
