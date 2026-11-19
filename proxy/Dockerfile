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
COPY proxy/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
