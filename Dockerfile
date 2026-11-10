# Sandbox image: every WideMind service in one container, for trying the
# agent with test money. The live pilot keeps the economy under its own user
# outside the agent's write access (docs/DEPLOYMENT.md); this image does not.
FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && useradd --create-home --uid 10001 widemind \
 && mkdir -p /app/var && chown -R widemind:widemind /app
WORKDIR /app
USER widemind
COPY --chown=widemind:widemind . .
# Install Node packages and the three isolated Python services at build time,
# so starting the container needs no network.
RUN npm ci \
 && node scripts/services.mjs setup \
 && rm -rf var/pip-cache
ENV WIDEMIND_HOST=0.0.0.0 \
    WIDEMIND_PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
# Creates the agent identity, service tokens and owner password on first
# start (the password is printed once in the logs), migrates, then runs the
# services, website and worker.
CMD ["node", "scripts/quickstart.mjs"]
