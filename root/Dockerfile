FROM node:24-alpine AS dashboard
WORKDIR /build
COPY package.json package-lock.json ./
COPY src/apps/web ./src/apps/web
COPY src/packages/api-client ./src/packages/api-client
COPY src/packages/shared-types ./src/packages/shared-types
RUN npm ci --no-audit --no-fund && npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=dashboard /build/src/apps/web/dist ./web
RUN pip install --no-deps . && useradd --uid 10001 --create-home postchief && mkdir -p /app/data/media && chown -R postchief:postchief /app/data
USER postchief
EXPOSE 8000
CMD ["uvicorn", "postchief.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
