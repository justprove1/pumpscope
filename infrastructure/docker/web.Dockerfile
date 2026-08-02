# STUB Fase 0: apps/web solo tiene un placeholder.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
COPY apps/web/package.json ./apps/web/
RUN npm install --omit=dev || npm install

FROM node:20-alpine AS runner
ENV NODE_ENV=production
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./apps/web/
COPY package.json ./
USER node
EXPOSE 3000
CMD ["npm", "run", "dev"]
