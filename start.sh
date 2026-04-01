#!/bin/bash
# Delivery Hub — Start the web server
cd "$(dirname "$0")"

echo "Starting Delivery Hub on http://127.0.0.1:8001"
exec venv/bin/python -m src.web.app
