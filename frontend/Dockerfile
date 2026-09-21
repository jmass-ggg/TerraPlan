# syntax=docker/dockerfile:1

FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json ./

RUN --mount=type=cache,target=/root/.npm \
    npm ci --include=dev

FROM node:22-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json ./

COPY app ./app
COPY components ./components
COPY features ./features
COPY hooks ./hooks
COPY lib ./lib
COPY public ./public
COPY photos ./photos
COPY .openai ./.openai
COPY next.config.ts tsconfig.json vite.config.ts postcss.config.mjs ./
COPY next-env.d.ts components.json ./

ARG NEXT_PUBLIC_FARMTWIN_API_URL
ENV NODE_ENV=production
ENV NEXT_PUBLIC_FARMTWIN_API_URL=${NEXT_PUBLIC_FARMTWIN_API_URL}

RUN npm run build \
    && find dist/standalone -type f \( \
      -name '*.map' -o -name '*.md' -o -name '*.test.*' -o -name '*.spec.*' \
    \) -delete \
    && find dist/standalone/node_modules -type d \( \
      -name test -o -name tests -o -name __tests__ \
    \) -prune -exec rm -rf '{}' +

FROM node:22-alpine AS runtime

WORKDIR /app

COPY --from=builder --chown=node:node /app/dist/standalone ./
# Vinext beta's standalone tracer omits its React peer dependencies.
COPY --from=deps --chown=node:node /app/node_modules/react ./node_modules/react
COPY --from=deps --chown=node:node /app/node_modules/react-dom ./node_modules/react-dom
COPY --from=deps --chown=node:node /app/node_modules/react-server-dom-webpack ./node_modules/react-server-dom-webpack
COPY --from=deps --chown=node:node /app/node_modules/scheduler ./node_modules/scheduler

ENV NODE_ENV=production
ENV PORT=3000
ENV HOST=0.0.0.0

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["node", "-e", "const r=require('http').get('http://127.0.0.1:3000/',x=>{x.resume();process.exit(x.statusCode>=200&&x.statusCode<400?0:1)});r.on('error',()=>process.exit(1));r.setTimeout(2000,()=>{r.destroy();process.exit(1)})"]

CMD ["node", "server.js"]
