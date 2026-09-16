from __future__ import annotations

from .app import create_app
from .config import CoatingApiSettings
from .orchestrator import CoatingConversationEngine


settings = CoatingApiSettings.from_env()
engine = CoatingConversationEngine(settings)
app = create_app(settings=settings, engine=engine)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
