from fastapi import FastAPI, Request
from pydantic import BaseModel
import imaplib
import email
from email.header import decode_header
import ssl

app = FastAPI()

# --- MODELO DE DATOS ---
class EmailRequest(BaseModel):
    email: str
    password: str = ""  # opcional, para cuentas que no usen app password
    imap_server: str = "imap.mail.me.com"  # iCloud por defecto
    limit: int = 5  # cantidad de mensajes a devolver

# --- FUNCIÓN PARA LEER CORREOS ---
def leer_correos(imap_server, email_user, password, limit):
    mensajes = []
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(imap_server, 993, ssl_context=context)
        mail.login(email_user, password)
        mail.select("INBOX")

        # Buscar los correos más recientes
        status, data = mail.search(None, "ALL")
        if status != "OK":
            return mensajes

        ids = data[0].split()
        ultimos = ids[-limit:]

        for num in reversed(ultimos):
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(data[0][1])
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")

            from_ = msg.get("From")
            date_ = msg.get("Date")

            # obtener texto plano
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    cdisp = str(part.get("Content-Disposition"))
                    if ctype == "text/plain" and "attachment" not in cdisp:
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

            mensajes.append({
                "subject": subject,
                "from": from_,
                "date": date_,
                "snippet": body[:200].replace("\n", " ")
            })

        mail.logout()
    except Exception as e:
        mensajes.append({"error": str(e)})

    return mensajes

# --- ENDPOINT PRINCIPAL ---
@app.post("/webhook")
async def webhook(req: EmailRequest):
    emails = leer_correos(req.imap_server, req.email, req.password, req.limit)
    return {"email": req.email, "messages": emails}
