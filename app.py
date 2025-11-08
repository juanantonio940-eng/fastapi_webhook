import os
import imaplib
import email as email_lib
import email.header
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Falta la variable de entorno DATABASE_URL")

app = FastAPI()


# ------- MODELOS -------

class WebhookInput(BaseModel):
    email: str  # correo que te llega por el webhook (MAIL_MADRE o ALIAS)


class Message(BaseModel):
    from_: str
    subject: str
    date: str
    text: str


class WebhookResponse(BaseModel):
    email: str
    messages: List[Message]


# ------- HELPERS DB -------

def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def get_account(email_in: str) -> Optional[dict]:
    """
    Busca en icloud_accounts una fila donde MAIL_MADRE = email
    o ALIAS = email. Devuelve usuario y password de iCloud.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    "MAIL_MADRE" AS icloud_user,
                    "PASSWORD"   AS icloud_app_password
                FROM "icloud_accounts"
                WHERE "MAIL_MADRE" = %s
                   OR "ALIAS"      = %s
                LIMIT 1
                """,
                (email_in, email_in),
            )
            row = cur.fetchone()
            return row
    finally:
        conn.close()


# ------- HELPERS IMAP (iCloud) -------

def decode_header_part(value: Optional[str]) -> str:
    if not value:
        return ""
    dh = email_lib.header.decode_header(value)[0]
    data, enc = dh
    if isinstance(data, bytes):
        try:
            return data.decode(enc or "utf-8", errors="ignore")
        except Exception:
            return data.decode("utf-8", errors="ignore")
    return data


def fetch_last_messages(
    icloud_user: str, icloud_pass: str, limit: int = 1
) -> List[Message]:
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(icloud_user, icloud_pass)
    imap.select("INBOX")

    status, data = imap.search(None, "ALL")
    if status != "OK":
        imap.logout()
        raise Exception("No se pudo buscar en el buzón")

    ids = data[0].split()
    if not ids:
        imap.logout()
        return []

    ids = ids[-limit:]  # últimos N

    messages: List[Message] = []

    for msg_id in ids:
        status, msg_data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_msg = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw_msg)

        subject = decode_header_part(msg.get("Subject"))
        from_ = decode_header_part(msg.get("From"))
        date_ = msg.get("Date") or ""

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if (
                    part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(errors="ignore")
                        break
        else:
            if msg.get_content_type() == "text/plain":
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="ignore")

        messages.append(
            Message(
                from_=from_,
                subject=subject,
                date=date_,
                text=body,
            )
        )

    imap.logout()
    return messages


# ------- RUTAS -------

@app.get("/")
def home():
    return {"status": "ok", "mensaje": "FastAPI + Supabase + iCloud listo"}


@app.post("/webhook", response_model=WebhookResponse)
def handle_webhook(payload: WebhookInput):
    # 1) Buscar la cuenta en Supabase
    account = get_account(payload.email)
    if not account:
        raise HTTPException(
            status_code=404,
            detail="Cuenta no encontrada en icloud_accounts para ese email",
        )

    icloud_user = account["icloud_user"]
    icloud_pass = account["icloud_app_password"]

    # 2) Leer correos de iCloud
    try:
        messages = fetch_last_messages(icloud_user, icloud_pass, limit=1)
    except imaplib.IMAP4.error as e:
        raise HTTPException(status_code=401, detail=f"Error autenticando en iCloud: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo correo: {e}")

    return WebhookResponse(email=payload.email, messages=messages)
