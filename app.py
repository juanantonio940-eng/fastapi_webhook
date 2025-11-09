from fastapi import FastAPI, Request
import imaplib
import email
from email.header import decode_header
import ssl

app = FastAPI()

# Servidores IMAP por proveedor
IMAP_SERVERS = {
    "icloud.com": "imap.mail.me.com",
    "gmail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "gmx.com": "imap.gmx.com",
    "web.de": "imap.web.de",
    "zoho.eu": "imap.zoho.eu",
}

def get_server(email_address):
    domain = email_address.split("@")[-1]
    return IMAP_SERVERS.get(domain, None)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    email_address = data.get("email")
    password = data.get("password")  # Contraseña de aplicación
    result = {"email": email_address, "messages": []}

    server = get_server(email_address)
    if not server:
        result["messages"].append({"error": "Proveedor no soportado"})
        return result

    try:
        # Conexión segura con SSL
        context = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL(server, 993, ssl_context=context)

        # Inicio de sesión con contraseña de aplicación
        imap.login(email_address, password)

        # Seleccionamos la bandeja de entrada
        imap.select("INBOX")

        # Buscamos los últimos 5 mensajes
        status, messages = imap.search(None, "ALL")
        if status != "OK":
            result["messages"].append({"error": "No se pudieron obtener los mensajes"})
            return result

        mail_ids = messages[0].split()[-5:]
        for mail_id in mail_ids:
            _, msg_data = imap.fetch(mail_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")

            result["messages"].append({
                "from": msg.get("From"),
                "subject": subject,
            })

        imap.close()
        imap.logout()

    except imaplib.IMAP4.error as e:
        result["messages"].append({"error": f"IMAP error: {str(e)}"})
    except Exception as e:
        result["messages"].append({"error": str(e)})

    return result
