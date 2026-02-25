#!/bin/bash
set -e

echo "Building Card UI Assets..."

# Go to embedded card-ui directory
cd src/entari_plugin_hyw/entari_plugin_hyw/core/tools/_public/browser/card-ui

# Install dependencies
echo "Installing dependencies..."
npm install

# Build
echo "Running Vite build..."
npm run build

echo "Done. Assets built to src/entari_plugin_hyw/entari_plugin_hyw/core/tools/_public/browser/assets/card-dist"
