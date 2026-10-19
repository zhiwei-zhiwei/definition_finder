FROM node:20-alpine AS build
WORKDIR /app
RUN npm install -g pnpm@10.33.0
# Lockfile first: the install layer is reused until dependencies change
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
# NEXT_PUBLIC_* is inlined into the JS bundle at build time, so it is a build arg, not a runtime env
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN pnpm build

# Runtime: only the standalone server (traced deps) + static assets, no pnpm or full node_modules
FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
