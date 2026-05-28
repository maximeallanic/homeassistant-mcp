#!/bin/bash
# Home Assistant MCP Server - Startup script for Personas Studio / Claude agents
# Loads credentials from .env and starts the MCP server via stdio
cd "$(dirname "$0")"

# Load .env if present
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Validate required environment variables
if [ -z "$HA_URL" ] || [ -z "$HA_TOKEN" ]; then
    echo "Error: HA_URL and HA_TOKEN must be set in .env or as environment variables" >&2
    exit 1
fi

exec .venv/bin/python server.py
