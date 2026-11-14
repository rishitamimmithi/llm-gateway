# Build the React app, then serve the static files with nginx (which also forwards API calls to the gateway)
FROM node:20-alpine AS build
WORKDIR /dashboard
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dashboard/dist /usr/share/nginx/html
EXPOSE 80
