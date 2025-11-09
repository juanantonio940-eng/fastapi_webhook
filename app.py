from fastapi import FastAPI, Request
import imaplib
import email
from email.header import decode_header
import ssl
import sqlite3  # Puedes cambiarlo por MySQL o PostgreSQL según tu entorno

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

def get_password_from_db(email_address: str):
    """
    Obtiene la contraseña asociada al email desde la base de datos.
    Ajusta los nombres de tabla y columna según tu estructura real.
    """
    conn = sqlite3.connect("usuarios.db")  # Cambia por tu conexión real
    cursor = conn.cursor()
    cursor.execute("SELECT password FROM cuentas WHERE email = ?", (email_address,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    email_address = data.get("email")
    result = {"email": email_address, "messages": []}

    # Recuperar contraseña desde la BD
    password = get_password_from_db(email_address)
    if not password:
        result["messages"].append({"error": f"No se encontró contraseña para {email_address}"})
        return result

    server = get_server(email_address)
    if not server:
        result["messages"].append({"error": "Proveedor no soportado"})
        return result

    try:
        # Conexión segura con SSL
        context = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL(server, 993, ssl_context=context)

        # Inicio de sesión con contraseña recuperada
        imap.login(email_address, password)
        imap.select("INBOX")

        # Obtener los últimos 5 correos
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
