import os
import imaplib
import email as email_lib
import email.header
from typing import List, Optional
import logging

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Configura logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    """
    Decodifica cualquier encabezado MIME (Subject, From, etc.)
    """
    if not value:
        return ""
    try:
        decoded_parts = email_lib.header.decode_header(value)
        decoded_str = ""
        for part, enc in decoded_parts:
            if isinstance(part, bytes):
                decoded_str += part.decode(enc or "utf-8", errors="ignore")
            elif isinstance(part, str):
                decoded_str += part
            else:
                decoded_str += str(part)
        return decoded_str
    except Exception:
        return str(value)


def fetch_last_messages(icloud_user: str, icloud_pass: str, limit: int = 1) -> List[Message]:
    """
    Conecta con iCloud IMAP y devuelve los últimos N mensajes NO LEÍDOS con asunto FIFA.
    """
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(icloud_user, icloud_pass)
        logger.info(f"✅ Login exitoso para {icloud_user}")
    except imaplib.IMAP4.error as e:
        raise Exception(f"Error autenticando en iCloud: {e}")

    imap.select("INBOX")

    # Buscar solo mensajes NO LEÍDOS
    logger.info("🔍 Buscando mensajes NO LEÍDOS (UNSEEN)")
    
    status, data = imap.search(None, "UNSEEN")
    logger.info(f"📧 Status de búsqueda: {status}")
    
    if status != "OK" or not data or not data[0]:
        logger.warning("⚠️ No se encontraron mensajes no leídos")
        imap.logout()
        return []

    unread_ids = data[0].split()
    logger.info(f"📬 Total de mensajes NO LEÍDOS: {len(unread_ids)}")
    
    # Procesar de atrás hacia adelante para encontrar los últimos emails de FIFA
    fifa_messages: List[Message] = []
    
    # Invertir la lista para empezar por los más recientes
    for msg_id in reversed(unread_ids):
        if len(fifa_messages) >= limit:
            break
            
        logger.info(f"📩 Procesando mensaje ID: {msg_id}")
        status, msg_data = imap.fetch(msg_id, "(RFC822)")
        
        if status != "OK" or not msg_data:
            logger.warning(f"⚠️ Error fetching mensaje {msg_id}")
            continue

        # Extraer raw_msg
        raw_msg = None
        for part in msg_data:
            if isinstance(part, tuple):
                if len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw_msg = part[1]
                    break
            elif isinstance(part, (bytes, bytearray)):
                raw_msg = part
                break

        if not raw_msg:
            logger.warning(f"⚠️ No se pudo extraer raw_msg del mensaje {msg_id}")
            continue

        msg = email_lib.message_from_bytes(raw_msg)

        subject = decode_header_part(msg.get("Subject"))
        from_ = decode_header_part(msg.get("From"))
        date_ = msg.get("Date") or ""
        
        logger.info(f"📨 Subject: '{subject}', From: '{from_}'")
        
        # Filtrar por asunto FIFA (si el subject está vacío, no es de FIFA)
        if not subject or ("FIFA ID" not in subject and "Validate Your Email" not in subject):
            logger.info(f"⏭️ Saltando mensaje - no es de FIFA")
            continue
        
        logger.info(f"🎯 ¡Encontrado mensaje de FIFA!")

        body = ""
        if msg.is_multipart():
            logger.info("📄 Mensaje es multipart")
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                if (
                    content_type == "text/plain"
                    and "attachment" not in content_disposition
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            body = payload.decode(errors="ignore")
                            logger.info(f"✅ Body extraído (multipart), tamaño: {len(body)} chars")
                        except Exception as e:
                            body = str(payload)
                            logger.warning(f"⚠️ Error decodificando body: {e}")
                        break
        else:
            logger.info("📄 Mensaje es single-part")
            if msg.get_content_type() == "text/plain":
                payload = msg.get_payload(decode=True)
                if payload:
                    try:
                        body = payload.decode(errors="ignore")
                        logger.info(f"✅ Body extraído (single-part), tamaño: {len(body)} chars")
                    except Exception as e:
                        body = str(payload)
                        logger.warning(f"⚠️ Error decodificando body: {e}")

        fifa_messages.append(
            Message(
                from_=from_,
                subject=subject,
                date=date_,
                text=body,
            )
        )
        logger.info(f"✅ Mensaje FIFA agregado correctamente a la lista")

    imap.logout()
    logger.info(f"📊 Total mensajes FIFA procesados: {len(fifa_messages)}")
    return fifa_messages


# ------- RUTAS -------

@app.get("/")
def home():
    return {"status": "ok", "mensaje": "FastAPI + Supabase + iCloud listo"}


@app.post("/webhook", response_model=WebhookResponse)
def handle_webhook(payload: WebhookInput):
    logger.info(f"🎯 Webhook recibido para email: {payload.email}")
    
    # 1) Buscar la cuenta en Supabase
    account = get_account(payload.email)
    if not account:
        logger.error(f"❌ Cuenta no encontrada para {payload.email}")
        raise HTTPException(
            status_code=404,
            detail="Cuenta no encontrada en icloud_accounts para ese email",
        )

    icloud_user = account["icloud_user"]
    icloud_pass = account["icloud_app_password"]
    logger.info(f"🔑 Credenciales encontradas para: {icloud_user}")

    # 2) Leer correos de iCloud
    try:
        messages = fetch_last_messages(icloud_user, icloud_pass, limit=1)
        logger.info(f"✅ Mensajes obtenidos: {len(messages)}")
    except imaplib.IMAP4.error as e:
        logger.error(f"❌ Error IMAP: {e}")
        raise HTTPException(status_code=401, detail=f"Error autenticando en iCloud: {e}")
    except Exception as e:
        logger.error(f"❌ Error general: {e}")
        raise HTTPException(status_code=500, detail=f"Error leyendo correo: {e}")

    return WebhookResponse(email=payload.email, messages=messages)
