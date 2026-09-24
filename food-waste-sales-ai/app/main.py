"""廚餘機業務 AI — web chat server.

Run:  uvicorn app.main:app --reload
"""

import asyncio
import csv
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

import anthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .prompts import CUSTOMER_PROMPT, SALES_PROMPT

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_FILE = BASE_DIR / "knowledge" / "products.md"
LEADS_FILE = BASE_DIR / "data" / "leads.csv"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
# When set, the sales-assistant mode and the leads list require this token.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

MAX_MESSAGES = 40
MAX_CHARS_PER_MESSAGE = 4000
MAX_TOOL_ROUNDS = 5

LEAD_FIELDS = ["time", "name", "phone", "line_id", "household_size", "interested_model", "needs", "contact_time"]

SAVE_LEAD_TOOL = {
    "name": "save_lead",
    "description": (
        "把有興趣的潛在客戶資料存起來，讓業務後續聯絡。"
        "只有在客戶同意留下資料、且已取得姓名與至少一種聯絡方式（電話或 LINE）後才呼叫。"
    ),
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "客戶姓名或稱呼"},
            "phone": {"type": "string", "description": "電話，沒有則留空字串"},
            "line_id": {"type": "string", "description": "LINE ID，沒有則留空字串"},
            "household_size": {"type": "string", "description": "家庭人數，不知道則留空字串"},
            "interested_model": {"type": "string", "description": "有興趣的機型，不確定則留空字串"},
            "needs": {"type": "string", "description": "需求摘要：空間、預算、在意的點等"},
            "contact_time": {"type": "string", "description": "方便聯絡的時間，沒提則留空字串"},
        },
        "required": ["name", "phone", "line_id", "needs"],
    },
}

client = anthropic.AsyncAnthropic()
app = FastAPI(title="廚餘機業務 AI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
_leads_lock = asyncio.Lock()


def load_knowledge() -> str:
    return KNOWLEDGE_FILE.read_text(encoding="utf-8")


def check_admin(token: str | None) -> None:
    if ADMIN_TOKEN and not secrets.compare_digest(token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="需要業務密碼")


def validate_lead(data: object) -> dict[str, str] | None:
    """Return a cleaned lead, or None if the tool input is unusable."""
    if not isinstance(data, dict):
        return None
    lead = {k: str(data.get(k, "") or "").strip()[:500] for k in LEAD_FIELDS if k != "time"}
    if not lead["name"] or not (lead["phone"] or lead["line_id"]):
        return None
    return lead


async def save_lead(lead: dict[str, str]) -> None:
    async with _leads_lock:
        LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
        is_new = not LEADS_FILE.exists()
        # utf-8-sig so Excel opens the Chinese text correctly
        with LEADS_FILE.open("a", newline="", encoding="utf-8-sig" if is_new else "utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LEAD_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({"time": datetime.now().isoformat(timespec="seconds"), **lead})


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CHARS_PER_MESSAGE)


class ChatRequest(BaseModel):
    mode: Literal["customer", "sales"] = "customer"
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)


async def run_chat(req: ChatRequest):
    """Stream the assistant's reply as plain text, running save_lead when Claude calls it."""
    prompt = CUSTOMER_PROMPT if req.mode == "customer" else SALES_PROMPT
    system = [{"type": "text", "text": prompt.format(knowledge=load_knowledge()), "cache_control": {"type": "ephemeral"}}]
    tools = [SAVE_LEAD_TOOL] if req.mode == "customer" else []
    messages: list[dict] = [{"role": m.role, "content": m.content} for m in req.messages if m.content.strip()]

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            async with client.beta.messages.stream(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield event.text
                response = await stream.get_final_message()
        except ValueError:
            # Tool input arrived as malformed JSON (eager input streaming); ask the user to retry.
            yield "\n\n（系統處理資料時發生問題，麻煩您再傳一次訊息。）"
            return
        except anthropic.RateLimitError:
            yield "\n\n（目前詢問人數較多，請稍後再試。）"
            return
        except anthropic.AuthenticationError:
            yield "（尚未設定 ANTHROPIC_API_KEY，請參考 README。）"
            return
        except anthropic.APIStatusError as e:
            yield f"\n\n（AI 服務暫時無法回應，錯誤代碼 {e.status_code}，請稍後再試。）"
            return
        except anthropic.APIConnectionError:
            yield "\n\n（連線到 AI 服務失敗，請檢查網路後再試。）"
            return

        if response.stop_reason == "refusal":
            yield "抱歉，這個問題我沒辦法回答。若有廚餘機相關問題，歡迎繼續詢問！"
            return
        if response.stop_reason != "tool_use":
            return

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            lead = validate_lead(block.input) if block.name == "save_lead" else None
            if lead is None:
                results.append({
                    "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": "資料不完整：需要姓名，以及電話或 LINE 其中之一。",
                })
                continue
            await save_lead(lead)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": "已成功登記，業務會盡快聯絡。"})
        messages.append({"role": "user", "content": results})
        yield "\n\n"


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/api/chat")
async def chat(req: ChatRequest, x_admin_token: str | None = Header(default=None)):
    if req.mode == "sales":
        check_admin(x_admin_token)
    if req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="最後一則訊息必須是使用者訊息")
    return StreamingResponse(run_chat(req), media_type="text/plain; charset=utf-8")


@app.get("/api/leads")
async def leads(x_admin_token: str | None = Header(default=None)):
    check_admin(x_admin_token)
    if not LEADS_FILE.exists():
        return []
    with LEADS_FILE.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@app.get("/api/config")
async def config():
    return {"sales_requires_token": bool(ADMIN_TOKEN)}
