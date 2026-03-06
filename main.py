from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import openai
import os
import tempfile
import re
import json

app = FastAPI(title="CallCoach API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    role: str
    content: str

class AnalyzeRequest(BaseModel):
    api_key: str
    transcript: str
    manager_name: str
    client_name: str
    call_type: str
    active_params: Optional[List[dict]] = []
    weights: Optional[dict] = {}

class ChatRequest(BaseModel):
    api_key: str
    messages: List[Message]
    system: str

class VerifyRequest(BaseModel):
    api_key: str

@app.get("/")
def root():
    return {"status": "ok", "message": "CallCoach API ready"}

@app.get("/health")
def health():
    return {"status": "ok", "message": "CallCoach API ready"}

@app.post("/verify-key")
async def verify_key(req: VerifyRequest):
    try:
        client = openai.OpenAI(api_key=req.api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5
        )
        return {"valid": True, "message": f"GPT-4o работает! Ответ: {response.choices[0].message.content}"}
    except openai.AuthenticationError:
        return {"valid": False, "message": "Неверный API ключ"}
    except Exception as e:
        return {"valid": False, "message": str(e)}

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), api_key: str = ""):
    if not api_key:
        raise HTTPException(status_code=400, detail="API key required")
    suffix = "." + (file.filename.split(".")[-1] if file.filename and "." in file.filename else "mp3")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        client = openai.OpenAI(api_key=api_key)
        with open(tmp_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                language="ru"
            )
        segments = []
        if hasattr(response, 'segments') and response.segments:
            for seg in response.segments:
                minutes = int(seg.start // 60)
                seconds = int(seg.start % 60)
                segments.append({
                    "time": f"{minutes:02d}:{seconds:02d}",
                    "text": seg.text.strip(),
                    "start": seg.start,
                    "end": seg.end
                })
        else:
            segments = [{"time": "00:00", "text": response.text, "start": 0, "end": 0}]
        return {
            "text": response.text,
            "segments": segments,
            "duration": segments[-1]["end"] if segments else 0,
            "language": getattr(response, 'language', 'ru')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)

@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        client = openai.OpenAI(api_key=req.api_key)
        params_text = ""
        if req.active_params:
            params_list = "\n".join([
                f"- {p.get('label', '')} (вес {req.weights.get(p.get('key',''), 0)}%): {p.get('desc', '')}"
                for p in req.active_params
            ])
            params_text = f"\n\nОЦЕНИВАЙ СТРОГО ПО ЭТИМ ПАРАМЕТРАМ:\n{params_list}"
        system_prompt = f"""Ты — AI-коуч по продажам для онлайн-школы.
Анализируешь звонки менеджеров и даёшь детальный разбор руководителю.
Отвечай на русском языке. Используй эмодзи для структуры.
Давай конкретные готовые фразы и скрипты.{params_text}"""
        user_prompt = f"""Сделай полный разбор звонка менеджера.

Менеджер: {req.manager_name}
Клиент: {req.client_name}
Тип звонка: {req.call_type}

ТРАНСКРИПТ ЗВОНКА:
{req.transcript[:6000]}

Структура разбора:
1. **Общая оценка** — что произошло (2-3 предложения)
2. **Что сделано хорошо** — 2-3 конкретных момента с цитатами
3. **Главные ошибки** — 3-4 ошибки с объяснением и цитатами
4. **Как надо было** — для каждой ошибки готовая фраза/скрипт
5. **Оценка по параметрам** — балл от 0 до 100 в формате JSON:
{{"scores": {{"param_key": score}}, "total": итоговый_балл}}
6. **Итог для руководителя** — что проработать с менеджером"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=2000
        )
        text = response.choices[0].message.content or ""
        scores = {}
        total = 50
        json_match = re.search(r'\{"scores":\s*\{[^}]+\},\s*"total":\s*\d+\}', text)
        if json_match:
            try:
                scores_data = json.loads(json_match.group())
                scores = scores_data.get("scores", {})
                total = scores_data.get("total", 50)
            except:
                pass
        return {"analysis": text, "scores": scores, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        client = openai.OpenAI(api_key=req.api_key)
        messages = [{"role": "system", "content": req.system}]
        for msg in req.messages:
            messages.append({"role": msg.role, "content": msg.content})
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000
        )
        return {"reply": response.choices[0].message.content or ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
