# Full-Stack AI Template

A production-ready template for building AI-powered applications with:

- **Backend**: Python + FastAPI + Anthropic Claude (Agentic AI)
- **Frontend**: Vite + React + TypeScript + shadcn/ui + AI SDK

## Project Structure

```
.
├── backend/                 # FastAPI backend
│   ├── app/
│   │   ├── api/            # API routes
│   │   ├── agents/         # AI agents
│   │   ├── core/           # Config, settings
│   │   ├── models/         # Pydantic models
│   │   └── services/       # Business logic
│   └── pyproject.toml
├── frontend/                # Vite + React frontend
│   ├── src/
│   │   ├── components/     # React components
│   │   │   └── ui/        # shadcn/ui components
│   │   ├── hooks/          # Custom hooks
│   │   ├── lib/            # Utilities
│   │   └── pages/          # Page components
│   └── package.json
├── script/                  # Development scripts
└── README.md
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- [uv](https://docs.astral.sh/uv/) (Python package manager)

## Quick Start

### 1. Setup

```bash
# Linux/macOS
./script/setup.sh

# Windows (PowerShell)
.\script\setup.ps1
```

### 2. Configure Environment

Edit `backend/.env` with your API keys:

```env
ANTHROPIC_API_KEY=your-api-key-here
```

### 3. Run Development Servers

```bash
# Linux/macOS
./script/dev.sh

# Windows (PowerShell)
.\script\dev.ps1
```

- Backend: http://localhost:8000
- Frontend: http://localhost:5173
- API Docs: http://localhost:8000/docs

## Manual Setup

### Backend

```bash
cd backend
cp .env.example .env
# Edit .env with your API keys
uv sync
uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/chat` | POST | Send chat message |
| `/api/chat/stream` | POST | Stream chat response (SSE) |

## Adding shadcn/ui Components

```bash
cd frontend
npx shadcn@latest add <component-name>
```

## Creating New Agents

Extend the `BaseAgent` class in `backend/app/agents/`:

```python
from app.agents.base import BaseAgent

class MyAgent(BaseAgent):
    @property
    def system_prompt(self) -> str:
        return "You are a specialized assistant for..."
```

## Tech Stack

### Backend
- [FastAPI](https://fastapi.tiangolo.com/) - Modern Python web framework
- [Anthropic SDK](https://docs.anthropic.com/) - Claude AI integration
- [Pydantic](https://docs.pydantic.dev/) - Data validation
- [uv](https://docs.astral.sh/uv/) - Fast Python package manager

### Frontend
- [Vite](https://vitejs.dev/) - Build tool
- [React](https://react.dev/) - UI framework
- [TypeScript](https://www.typescriptlang.org/) - Type safety
- [shadcn/ui](https://ui.shadcn.com/) - UI components
- [Tailwind CSS](https://tailwindcss.com/) - Styling
- [Vercel AI SDK](https://sdk.vercel.ai/) - AI integration utilities

## License

MIT
