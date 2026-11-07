FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# Public values baked into the bundle (a browser DSN is not a secret). Empty: Sentry is off.
ARG VITE_SENTRY_DSN=""
ARG VITE_APP_RELEASE=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN VITE_APP_RELEASE=$VITE_APP_RELEASE
RUN pnpm build

# Runs as the unprivileged `nginx` user and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
