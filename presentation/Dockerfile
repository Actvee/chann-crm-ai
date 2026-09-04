FROM node:22-alpine AS deps
WORKDIR /srv
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi

FROM node:22-alpine AS build
WORKDIR /srv
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /srv
ENV NODE_ENV=production PORT=3000
COPY --from=build /srv/.next/standalone ./
# Next's standalone output does not include public/ — without this line every
# file under presentation/public (the CSV samples) is a 404 in production.
COPY --from=build /srv/public ./public
COPY --from=build /srv/.next/static ./.next/static
CMD ["node", "server.js"]
