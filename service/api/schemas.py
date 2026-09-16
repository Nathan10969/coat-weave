from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatOptions(BaseModel):
    include_debug: bool = False
    stream: bool = False
    mock_model: bool = False


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str = "default"
    user_id: str | None = None
    options: ChatOptions = Field(default_factory=ChatOptions)


class OpenAIChatMessage(BaseModel):
    role: str
    content: Any = ""


class OpenAIChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[OpenAIChatMessage] = Field(min_length=1)
    stream: bool = True
    user: str | None = None
    conversation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: ChatOptions = Field(default_factory=ChatOptions)

    def question_text(self) -> str:
        selected = next((message for message in reversed(self.messages) if message.role == "user"), self.messages[-1])
        return _message_content_to_text(selected.content).strip()

    def to_chat_request(self, *, session_id: str | None = None) -> ChatRequest:
        conversation_id = (
            session_id
            or self.conversation_id
            or str(self.metadata.get("sessionId") or "").strip()
            or str(self.metadata.get("session_id") or "").strip()
            or str(self.metadata.get("conversation_id") or "").strip()
            or str(self.metadata.get("conversationId") or "").strip()
            or self.user
            or "default"
        )
        return ChatRequest(
            question=self.question_text(),
            conversation_id=conversation_id,
            user_id=self.user,
            options=ChatOptions(
                include_debug=self.options.include_debug,
                stream=self.stream,
                mock_model=self.options.mock_model,
            ),
        )


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text" and part.get("text") is not None:
                    parts.append(str(part["text"]))
                elif part.get("text") is not None:
                    parts.append(str(part["text"]))
                elif part.get("content") is not None:
                    parts.append(str(part["content"]))
            elif part is not None:
                parts.append(str(part))
        return "\n".join(parts)
    if isinstance(content, dict):
        if content.get("text") is not None:
            return str(content["text"])
        if content.get("content") is not None:
            return str(content["content"])
    return str(content)


class ToolCallSummary(BaseModel):
    tool: str
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    doc_id: str | None = None
    object_id: str | None = None
    page: int | None = None
    section: str | None = None
    table: str | None = None
    quote: str | None = None


class ChatResponse(BaseModel):
    answer: str
    provider: str
    model: str
    conversation_id: str
    user_id: str | None = None
    active_scope: dict[str, Any] | None = None
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] | None = None


class ToolProxyRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "coating-api"
