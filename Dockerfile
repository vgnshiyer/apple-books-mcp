# Deprecated (see README): a Linux container can't be granted the macOS
# permission, reach iCloud book files or tell whether Books is running.
# Pinned by digest; Dependabot proposes updates.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

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
