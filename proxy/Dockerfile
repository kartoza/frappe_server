# Build context is the repo root (see docker-compose.yml) so this stage can
# reach into web/ to build the React app.
FROM node:20-alpine AS build
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM nginx:1.27-alpine
RUN mkdir -p /etc/nginx/conf.d/sites
# nginx's entrypoint envsubst's every /etc/nginx/templates/*.template into
# NGINX_ENVSUBST_OUTPUT_DIR, stripping the suffix — pointed at /etc/nginx
# (rather than its default of /etc/nginx/conf.d) so the result lands at the
# main config path nginx actually reads on boot.
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx
COPY proxy/nginx.conf /etc/nginx/templates/nginx.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
