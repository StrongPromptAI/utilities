FROM node:22-slim AS build
WORKDIR /app

# Install dependencies first (layer cache)
COPY dashboard/frontend/package*.json ./
RUN npm ci

# Copy frontend source
COPY dashboard/frontend/ .

RUN npm run build

FROM node:22-slim
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/dist ./dist
CMD ["sh", "-c", "serve -s dist -l ${PORT:-8080}"]
