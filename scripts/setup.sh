#!/bin/bash

# Setup script for the project.
#
# Usage:
#   ./scripts/setup.sh
#   ./scripts/setup.sh local-models
#
# Extras:
#   asr-api-vad          Silero ONNX VAD chunking for API ASR
#   local-asr            local Qwen3-ASR + Pyannote
#   uvr                  UVR vocal separation
#   hf-local-inference   torch + accelerate for local HF inference
#   local-models         full local model stack

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Setting up project...${NC}"

# Check prerequisites
echo -e "\n${YELLOW}Checking prerequisites...${NC}"

if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv is not installed. Install it from https://docs.astral.sh/uv/${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js is not installed.${NC}"
    exit 1
fi

echo -e "${GREEN}Prerequisites OK${NC}"

# Setup backend
echo -e "\n${YELLOW}Setting up backend...${NC}"
EXTRA="${1:-}"

if [ -f backend/.env.example ] && [ ! -f backend/.env ]; then
    cd backend
    cp .env.example .env
    cd ..
    echo -e "${YELLOW}Created .env file - please update with your API keys${NC}"
fi

if [ -n "$EXTRA" ]; then
    uv sync --extra "$EXTRA"
else
    uv sync
fi
echo -e "${GREEN}Backend dependencies installed${NC}"

# Setup frontend
echo -e "\n${YELLOW}Setting up frontend...${NC}"
cd web
npm install
echo -e "${GREEN}Frontend dependencies installed${NC}"
cd ..

echo -e "\n${GREEN}Setup complete!${NC}"
echo -e "1. Update ${YELLOW}backend/.env${NC} with your API keys"
echo -e "2. Run ${YELLOW}./scripts/dev.sh${NC} to start development servers"
