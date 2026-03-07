from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import openai
import os
import tempfile

app = FastAPI(title="CallCoach API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_client(api_key: str):
    return openai.OpenAI(api_key=api_key)


# ─── HEALTH CHECK ───
@app.get("/")
def root():
    return {"status": "ok", "message": "CallCoach API ready"}


# ─── VERIFY KEY ───
class VerifyRequest(BaseModel):
    api_key: str

@app.post("/verify-key")
def verify_key(req: VerifyRequest):
    try:
        client = get_client(req.api_key)
        client.models.list()
        return {"ok": True, "message": "Ключ валиден"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── TRANSCRIBE ───
@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    api_key: str = ""
):
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Нет API ключа")

    try:
        client = get_client(api_key)

        # Сохраняем файл во временный файл
        suffix = "." + (file.filename.split(".")[-1] if file.filename else "mp3")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Транскрибируем через Whisper
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru",
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        os.unlink(tmp_path)

        # Форматируем транскрипт с таймкодами
        segments = []
        if hasattr(transcript, "segments") and transcript.segments:
            for seg in transcript.segments:
                minutes = int(seg.start // 60)
                seconds = int(seg.start % 60)
                segments.append({
                    "time": f"{minutes:02d}:{seconds:02d}",
                    "text": seg.text.strip(),
                    "start": seg.start,
                    "end": seg.end
                })
        else:
            segments = [{"time": "00:00", "text": transcript.text, "start": 0, "end": 0}]

        return {
            "text": transcript.text,
            "segments": segments,
            "duration": segments[-1]["end"] if segments else 0
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── ANALYZE ───
class AnalyzeRequest(BaseModel):
    transcript: str
    manager_name: str
    client_name: str
    call_type: str
    api_key: str
    scoring_params: Optional[List[dict]] = []
    scoring_weights: Optional[dict] = {}

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        client = get_client(req.api_key)

        # Формируем описание параметров оценки
        scoring_info = ""
        if req.scoring_params and req.scoring_weights:
            scoring_info = "\n\nПАРАМЕТРЫ ОЦЕНКИ (оцени каждый от 0 до 100):\n"
            for p in req.scoring_params:
                w = req.scoring_weights.get(p.get("key", ""), 0)
                scoring_info += f"- {p.get('label', '')} (вес {w}%): {p.get('desc', '')}\n"
            scoring_info += "\nИТОГОВЫЙ БАЛЛ = сумма (оценка_параметра × вес / 100)"

        prompt = f"""Ты — AI-коуч по продажам. Проанализируй звонок менеджера.

МЕНЕДЖЕР: {req.manager_name}
КЛИЕНТ: {req.client_name}
ТИП ЗВОНКА: {req.call_type}
{scoring_info}

ТРАНСКРИПТ ЗВОНКА:
{req.transcript[:6000]}

Дай структурированный разбор:

1. **Общая оценка** — что произошло в звонке (3-4 предложения)
2. **Что сделано хорошо** — 2-3 конкретных момента с цитатами из транскрипта
3. **Главные ошибки** — 3-4 ошибки с объяснением почему это плохо
4. **Как надо было** — для каждой ошибки покажи готовую фразу или скрипт
5. **Итог для руководителя** — что проработать на следующей планёрке

Также верни JSON с оценками параметров в конце ответа в формате:
```json
{{"scores": {{"pain": 0, "needs": 0, "objections": 0, "closing": 0}}, "total": 0, "result": "Продал/Думает/Не продал"}}
```"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.7
        )

        text = response.choices[0].message.content or ""

        # Извлекаем JSON с оценками
        import re, json
        scores = {}
        total = 50
        result = "Думает"

        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                scores = data.get("scores", {})
                total = data.get("total", 50)
                result = data.get("result", "Думает")
                # Убираем JSON блок из текста ответа
                text = text[:json_match.start()].strip()
            except:
                pass

        return {
            "analysis": text,
            "scores": scores,
            "total": total,
            "result": result
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── CHAT ───
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    system: str
    api_key: str

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        client = get_client(req.api_key)

        msgs = [{"role": "system", "content": req.system}]
        for m in req.messages:
            msgs.append({"role": m.role, "content": m.content})

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=msgs,
            max_tokens=1000,
            temperature=0.7
        )

        return {"reply": response.choices[0].message.content or ""}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

