FROM python:3.12-slim

# Runtime deps:
#   - ripgrep: goose shell tool + constantia's symbol-resolver grep
#   - curl + ca-certs: goose installer + Mistral API calls
#   - git: in-container sanity checks (Argo clones before us, but rg-over-worktree needs it on PATH)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ripgrep curl ca-certificates git libxcb1 bzip2 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Install goose as root so the binary lands on the shared PATH, then drop privs.
ENV GOOSE_BIN_DIR=/usr/local/bin \
    CONFIGURE=false
RUN curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh | bash \
 && goose --version

RUN groupadd -g 1000 constantia && useradd -u 1000 -g constantia -m constantia

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY recipes/ recipes/
COPY examples/ examples/
COPY schemas/ schemas/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

USER constantia

ENTRYPOINT ["python3", "-m", "constantia.cli"]
CMD ["--help"]
