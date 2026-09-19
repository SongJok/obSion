# syntax=docker/dockerfile:1.7
FROM node:22.23.2-alpine3.24@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/sdk-ts/package.json packages/sdk-ts/package.json
RUN npm ci

FROM dependencies AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_OBSION_API_URL=http://localhost:8080/api/v1
ENV NEXT_PUBLIC_OBSION_API_URL=$NEXT_PUBLIC_OBSION_API_URL
COPY apps/web/ apps/web/
COPY packages/sdk-ts/ packages/sdk-ts/
RUN npm run build --workspace @obsion/sdk \
    && npm run build --workspace @obsion/web

FROM node:22.23.2-alpine3.24@sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85 AS runtime
# The standalone server only needs the Node runtime. Keeping package managers
# here would retain their transitive archive/extraction surface in production.
RUN rm -r /usr/local/lib/node_modules/npm \
        /usr/local/lib/node_modules/corepack \
        /opt/yarn-v1.22.22 \
    && rm /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
        /usr/local/bin/yarn /usr/local/bin/yarnpkg \
    && addgroup --system --gid 10001 obsion \
    && adduser --system --uid 10001 --ingroup obsion obsion
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=builder --chown=obsion:obsion /app/apps/web/.next/standalone ./
COPY --from=builder --chown=obsion:obsion /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=obsion:obsion /app/apps/web/public ./apps/web/public
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "apps/web/server.js"]
