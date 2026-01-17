#!/bin/bash

# Start backend and frontend in parallel

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}Starting development servers...${NC}"

# Check if we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo -e "${RED}Error: Run this script from the project root directory${NC}"
    exit 1
fi

# Function to cleanup on exit
cleanup() {
    echo -e "\n${GREEN}Stopping servers...${NC}"
    kill $(jobs -p) 2>/dev/null
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start backend
echo -e "${GREEN}Starting backend server...${NC}"
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
cd ..

# Start frontend
echo -e "${GREEN}Starting frontend server...${NC}"
cd frontend
npm run dev &
cd ..

echo -e "${GREEN}Servers started!${NC}"
echo -e "Backend:  http://localhost:8000"
echo -e "Frontend: http://localhost:5173"
echo -e "\nPress Ctrl+C to stop all servers"

wait
