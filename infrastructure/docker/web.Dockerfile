FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
COPY apps/web/package.json ./apps/web/
RUN npm ci || npm install
COPY apps/web/ ./apps/web/
RUN npm run build --workspace apps/web

FROM node:22-alpine AS runner
ENV NODE_ENV=production
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/apps/web ./apps/web
COPY --from=builder /app/package.json ./
USER node
EXPOSE 3000
CMD ["npm", "run", "start", "--workspace", "apps/web"]
