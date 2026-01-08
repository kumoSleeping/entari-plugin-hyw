#!/bin/bash
set -e

echo "Building Card UI Assets..."

# Go to card-ui directory
cd src/entari_plugin_hyw/card-ui

# Install dependencies if node_modules missing or package.json changed (naive check, just run install usually fast enough)
echo "Installing dependencies..."
npm install

# Build
echo "Running Vite build..."
npm run build

echo "Done. Assets built to src/entari_plugin_hyw/assets/card-dist"
