#!/bin/bash
echo "Building Tailwind CSS..."
cd src/entari_plugin_hyw/assets

# Initialize npm if package.json doesn't exist
if [ ! -f "package.json" ]; then
    echo "Initializing npm..."
    npm init -y
fi

# Install tailwindcss if not present
if [ ! -d "node_modules/tailwindcss" ]; then
    echo "Installing tailwindcss..."
    npm install tailwindcss
fi

# Run build using direct path
if [ -f "./node_modules/.bin/tailwindcss" ]; then
    ./node_modules/.bin/tailwindcss -i tailwind.input.css -o libs/tailwind.css --minify
else
    echo "Error: tailwindcss binary not found in node_modules/.bin"
    echo "Contents of node_modules/.bin:"
    ls -la node_modules/.bin
    exit 1
fi

echo "Done."
