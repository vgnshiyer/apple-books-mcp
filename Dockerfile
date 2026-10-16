# Deprecated (see README): a Linux container can't be granted the macOS
# permission, reach iCloud book files or tell whether Books is running.
# Pinned by digest; Dependabot proposes updates.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY apple_books_mcp ./apple_books_mcp

RUN pip install --no-cache-dir .

# Run unprivileged. The README mounts the library at /root/Library/...,
# so /root is made traversable and the library is looked for there.
RUN useradd --create-home --uid 10001 app && chmod 0711 /root
ENV APPLE_BOOKS_DATA_DIR=/root/Library/Containers/com.apple.iBooksX/Data/Documents
USER app

ENTRYPOINT ["apple-books-mcp"]
