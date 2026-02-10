#!/bin/bash
set -e

echo "Building Card UI Assets..."

# Go to card-ui directory (New Path)
cd src/hyw_core/hyw_core/tools/_public/browser/card-ui

# Install dependencies
echo "Installing dependencies..."
npm install

# Build
echo "Running Vite build..."
npm run build

echo "Done. Assets built to src/hyw_core/tools/public/browser/assets/card-dist"
