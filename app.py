from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class WebhookData(BaseModel):
    email: str

@app.get("/")
def home():
    return {"status": "ok", "mensaje": "FastAPI funcionando en EasyPanel"}

@app.post("/webhook")
def recibir_webhook(data: WebhookData):
    print(f"📩 Webhook recibido: {data.email}")
    return {"status": "ok", "email": data.email}
