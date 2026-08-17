FROM node:22-alpine
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

ENV PORT=3000
EXPOSE 3000
# `next start` (not standalone) so the /api rewrite target is read from the
# environment at container start, not baked at build time.
CMD ["npm", "run", "start"]
