#!/bin/bash

# Setup script for the project

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
cd backend

if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Created .env file - please update with your API keys${NC}"
fi

uv sync
echo -e "${GREEN}Backend dependencies installed${NC}"
cd ..

# Setup frontend
echo -e "\n${YELLOW}Setting up frontend...${NC}"
cd frontend
npm install
echo -e "${GREEN}Frontend dependencies installed${NC}"
cd ..

echo -e "\n${GREEN}Setup complete!${NC}"
echo -e "1. Update ${YELLOW}backend/.env${NC} with your API keys"
echo -e "2. Run ${YELLOW}./script/dev.sh${NC} to start development servers"
