import os
import uvicorn
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, validator
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from twilio.rest import Client

# =====================================================================
# 1. CONFIGURACIÓN DE LA BASE DE DATOS (SQLite)
# =====================================================================
DATABASE_URL = "sqlite:///./agenda_belleza.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CitaDB(Base):
    __tablename__ = "citas"

    id = Column(Integer, primary_key=True, index=True)
    nombre_cliente = Column(String, nullable=False)
    telefono = Column(String, nullable=False)
    servicio = Column(String, nullable=False)
    fecha_hora_inicio = Column(DateTime, nullable=False, index=True)
    fecha_hora_fin = Column(DateTime, nullable=False)

Base.metadata.create_all(bind=engine)

# =====================================================================
# 2. CATÁLOGO DE SERVICIOS ACTUALIZADO
# =====================================================================
SERVICIOS_DISPONIBLES = {
    "manicura": {"nombre": "Manicura", "duracion": 60},
    "poligel": {"nombre": "Poligel", "duracion": 120},
    "acrilico": {"nombre": "Acrilico", "duracion": 120},
    "tradicional": {"nombre": "Tradicional", "duracion": 90},
    "diping": {"nombre": "Diping", "duracion": 120},
    "semipermanente": {"nombre": "Semipermanente", "duracion": 120},
    "pedicura": {"nombre": "Pedicura", "duracion": 90},
    "cepillado": {"nombre": "Cepillado", "duracion": 50},
    "alisado": {"nombre": "Alisado", "duracion": 90},
    "alisado y secado": {"nombre": "Alisado y Secado", "duracion": 150}
}

# =====================================================================
# 3. CONFIGURACIÓN DE TWILIO (100% LIMPIA Y SEGURA PARA GITHUB)
# =====================================================================
# Leemos estrictamente de las variables de entorno de la nube.
# Si el servidor está en local (sin variables), usará strings vacíos por defecto.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")
MI_NUMERO_WHATSAPP = os.environ.get("MI_NUMERO_WHATSAPP", "")

# =====================================================================
# 4. CONFIGURACIÓN DE LA API (FastAPI)
# =====================================================================
app = FastAPI(title="API de Agendamiento Profesional de Citas")

# Middleware para habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CitaRequest(BaseModel):
    nombre_cliente: str
    telefono: str
    servicio: str
    fecha_hora: datetime

    @validator('servicio')
    def validar_servicio(cls, v):
        servicio_normalizado = v.strip().lower()
        if servicio_normalizado not in SERVICIOS_DISPONIBLES:
            servicios_validos = ", ".join([s.title() for s in SERVICIOS_DISPONIBLES.keys()])
            raise ValueError(f"Servicio no válido. Opciones: {servicios_validos}")
        return servicio_normalizado

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- ENDPOINT 1: CREAR CITA CON NOTIFICACIÓN AL ADMINISTRADOR ---
@app.post("/agendar-cita/")
def agendar_cita(cita: CitaRequest, db: Session = Depends(get_db)):
    servicio_info = SERVICIOS_DISPONIBLES[cita.servicio]
    duracion_minutos = servicio_info["duracion"]
    nombre_servicio_formato = servicio_info["nombre"]

    nueva_fecha_inicio = cita.fecha_hora
    nueva_fecha_fin = nueva_fecha_inicio + timedelta(minutes=duracion_minutos)

    # Validamos colisiones en el calendario
    cita_conflictiva = db.query(CitaDB).filter(
        CitaDB.fecha_hora_inicio < nueva_fecha_fin,
        CitaDB.fecha_hora_fin > nueva_fecha_inicio
    ).first()

    if cita_conflictiva:
        inicio_conflicto = cita_conflictiva.fecha_hora_inicio.strftime("%I:%M %p")
        fin_conflicto = cita_conflictiva.fecha_hora_fin.strftime("%I:%M %p")
        raise HTTPException(
            status_code=400,
            detail=f"Horario ocupado por una cita de '{cita_conflictiva.servicio}' ({inicio_conflicto} - {fin_conflicto})."
        )

    nueva_cita = CitaDB(
        nombre_cliente=cita.nombre_cliente,
        telefono=cita.telefono,
        servicio=nombre_servicio_formato,
        fecha_hora_inicio=nueva_fecha_inicio,
        fecha_hora_fin=nueva_fecha_fin
    )
    db.add(nueva_cita)
    db.commit()
    db.refresh(nueva_cita)
    
    # Armamos la plantilla de notificación
    fecha_legible = nueva_cita.fecha_hora_inicio.strftime("%d/%m/%Y a las %I:%M %p")
    mensaje_alerta = (
        f"🔔 *¡Nueva Cita Agendada!* 💅✨\n\n"
        f"👤 *Cliente:* {nueva_cita.nombre_cliente}\n"
        f"📞 *WhatsApp:* {nueva_cita.telefono}\n"
        f"💇‍♀️ *Servicio:* {nueva_cita.servicio}\n"
        f"📅 *Horario:* {fecha_legible}\n"
        f"⏳ *Duración:* {duracion_minutos} min (Finaliza {nueva_cita.fecha_hora_fin.strftime('%I:%M %p')})"
    )
    
    # Enviamos notificación por WhatsApp de forma segura
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        destino = MI_NUMERO_WHATSAPP.strip()
        if not destino.lower().startswith("whatsapp:"):
            destino = f"whatsapp:{destino}"
            
        client.messages.create(
            body=mensaje_alerta,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=destino
        )
        print("Notificación de nueva cita enviada al administrador.")
    except Exception as e:
        print(f"Error al enviar notificación de WhatsApp: {e}")
    
    return {
        "mensaje": "Cita agendada. Notificación enviada al administrador.",
        "cita_id": nueva_cita.id
    }

# --- ENDPOINT 2: VER TODAS LAS CITAS ---
@app.get("/citas/")
def obtener_todas_las_citas(db: Session = Depends(get_db)):
    return db.query(CitaDB).order_by(CitaDB.fecha_hora_inicio.asc()).all()

# --- ENDPOINT 3: ELIMINAR CITA ---
@app.delete("/citas/{cita_id}")
def eliminar_cita(cita_id: int, db: Session = Depends(get_db)):
    cita = db.query(CitaDB).filter(CitaDB.id == cita_id).first()
    if not cita:
        raise HTTPException(status_code=404, detail="Cita no encontrada")
    db.delete(cita)
    db.commit()
    return {"mensaje": f"Cita con ID {cita_id} eliminada con éxito"}

if __name__ == "__main__":
    # Render asigna un puerto dinámico en la variable de entorno PORT
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)