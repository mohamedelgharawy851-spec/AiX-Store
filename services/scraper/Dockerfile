FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY . .

# Default upstream FastAPI URL – override via HF Space secret AIXSTORE_FASTAPI_URL
ENV AIXSTORE_FASTAPI_URL=https://shadypro-aixstore-api.hf.space
ENV PORT=7860

EXPOSE 7860

CMD ["node", "server.mjs"]
