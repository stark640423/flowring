"""廚餘機業務 AI — web chat server (DeepSeek).

Run:  uvicorn app.main:app --reload
"""

import asyncio
import csv
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

import openai
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .prompts import CUSTOMER_PROMPT, SALES_PROMPT

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

KNOWLEDGE_FILE = BASE_DIR / "knowledge" / "products.md"
LEADS_FILE = BASE_DIR / "data" / "leads.csv"

# DeepSeek's API is OpenAI-compatible, so the openai package talks to it directly.
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# When set, the sales-assistant mode and the leads list require this token.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

MAX_MESSAGES = 40
MAX_CHARS_PER_MESSAGE = 4000
MAX_TOOL_ROUNDS = 5

LEAD_FIELDS = ["time", "name", "phone", "line_id", "household_size", "interested_model", "needs", "contact_time"]

SAVE_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "save_lead",
        "description": (
            "把有興趣的潛在客戶資料存起來，讓業務後續聯絡。"
            "只有在客戶同意留下資料、且已取得姓名與至少一種聯絡方式（電話或 LINE）後才呼叫。"
        ),
        "parameters": {
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
    },
}

client = openai.AsyncOpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY", "missing"), base_url=BASE_URL)
app = FastAPI(title="廚餘機業務 AI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
_leads_lock = asyncio.Lock()


def load_knowledge() -> str:
    return KNOWLEDGE_FILE.read_text(encoding="utf-8")


def check_admin(token: str | None) -> None:
    if ADMIN_TOKEN and not secrets.compare_digest(token or "", ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="需要業務密碼")


def validate_lead(arguments: str) -> dict[str, str] | None:
    """Return a cleaned lead from the tool-call JSON, or None if it is unusable."""
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return None
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
    """Stream the assistant's reply as plain text, running save_lead when the model calls it."""
    prompt = CUSTOMER_PROMPT if req.mode == "customer" else SALES_PROMPT
    messages: list[dict] = [{"role": "system", "content": prompt.format(knowledge=load_knowledge())}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages if m.content.strip()]
    tools = {"tools": [SAVE_LEAD_TOOL]} if req.mode == "customer" else {}

    for _ in range(MAX_TOOL_ROUNDS):
        text = ""
        calls: dict[int, dict[str, str]] = {}
        finish_reason = None
        try:
            stream = await client.chat.completions.create(
                model=MODEL, messages=messages, stream=True, max_tokens=4000, **tools
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    yield delta.content
                for tc in delta.tool_calls or []:
                    call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = tc.id or call["id"]
                    if tc.function:
                        call["name"] += tc.function.name or ""
                        call["arguments"] += tc.function.arguments or ""
                finish_reason = choice.finish_reason or finish_reason
        except openai.AuthenticationError:
            yield "（DeepSeek 金鑰無效或尚未設定，請檢查 .env 裡的 DEEPSEEK_API_KEY。）"
            return
        except openai.RateLimitError:
            yield "\n\n（目前詢問人數較多，請稍後再試。）"
            return
        except openai.APIStatusError as e:
            yield f"\n\n（AI 服務暫時無法回應，錯誤代碼 {e.status_code}，請稍後再試。）"
            return
        except openai.APIConnectionError:
            yield "\n\n（連線到 AI 服務失敗，請檢查網路後再試。）"
            return

        if finish_reason != "tool_calls" or not calls:
            return

        ordered = [calls[i] for i in sorted(calls)]
        messages.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in ordered
            ],
        })
        for c in ordered:
            lead = validate_lead(c["arguments"]) if c["name"] == "save_lead" else None
            if lead is None:
                result = "資料不完整：需要姓名，以及電話或 LINE 其中之一。"
            else:
                await save_lead(lead)
                result = "已成功登記，業務會盡快聯絡。"
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        if text:
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
