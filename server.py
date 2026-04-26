from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

# ---------- DB ----------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"

# ---------- Models ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str

class ProductIn(BaseModel):
    title: str
    description: str
    price: float
    category: str
    image_url: str
    features: List[str] = []
    badge: Optional[str] = None
    gallery: List[str] = []
    video_url: Optional[str] = None

class ProductOut(ProductIn):
    id: str
    created_at: str
    views: int = 0

class CouponIn(BaseModel):
    code: str
    discount_percent: float
    active: bool = True
    max_uses: int = 0  # 0 = unlimited
    uses_count: int = 0

class CouponOut(CouponIn):
    id: str
    created_at: str

class CouponValidateRequest(BaseModel):
    code: str

# ---------- Auth helpers ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
        "type": "access",
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return user

# ---------- App ----------
app = FastAPI(title="ZemDev API")
api_router = APIRouter(prefix="/api")

@api_router.get("/")
async def root():
    return {"message": "ZemDev API running"}
    
app.include_router(api_router)
# ----- Auth routes -----
@api_router.post("/auth/login")
async def login(payload: LoginRequest, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    token = create_access_token(user["id"], user["email"])
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=12 * 3600,
        path="/",
    )
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "token": token,
    }

@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"message": "Sesión cerrada"}

@api_router.get("/auth/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return UserOut(**user)

# ----- Products routes -----
@api_router.get("/products", response_model=List[ProductOut])
async def list_products(category: Optional[str] = None):
    query = {}
    if category and category.lower() != "all":
        query["category"] = category
    docs = await db.products.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    for d in docs:
        d.setdefault("views", 0)
        d.setdefault("gallery", [])
        d.setdefault("video_url", None)
    return docs

@api_router.get("/products/top", response_model=List[ProductOut])
async def top_products(limit: int = 3):
    docs = await db.products.find({}, {"_id": 0}).sort("views", -1).limit(limit).to_list(limit)
    for d in docs:
        d.setdefault("views", 0)
        d.setdefault("gallery", [])
        d.setdefault("video_url", None)
    return docs

@api_router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: str):
    doc = await db.products.find_one_and_update(
        {"id": product_id},
        {"$inc": {"views": 1}},
        return_document=True,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    doc.setdefault("views", 1)
    doc.setdefault("gallery", [])
    doc.setdefault("video_url", None)
    return doc

@api_router.post("/products", response_model=ProductOut)
async def create_product(payload: ProductIn, _: dict = Depends(require_admin)):
    doc = payload.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    doc["views"] = 0
    await db.products.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: str, payload: ProductIn, _: dict = Depends(require_admin)):
    update_data = payload.model_dump()
    result = await db.products.find_one_and_update(
        {"id": product_id},
        {"$set": update_data},
        return_document=True,
        projection={"_id": 0},
    )
    if not result:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    result.setdefault("views", 0)
    result.setdefault("gallery", [])
    result.setdefault("video_url", None)
    return result

@api_router.delete("/products/{product_id}")
async def delete_product(product_id: str, _: dict = Depends(require_admin)):
    res = await db.products.delete_one({"id": product_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return {"message": "Producto eliminado"}

# ----- Coupons -----
@api_router.get("/coupons", response_model=List[CouponOut])
async def list_coupons(_: dict = Depends(require_admin)):
    docs = await db.coupons.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return docs

@api_router.post("/coupons", response_model=CouponOut)
async def create_coupon(payload: CouponIn, _: dict = Depends(require_admin)):
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Código requerido")
    existing = await db.coupons.find_one({"code": code})
    if existing:
        raise HTTPException(status_code=400, detail="Ese código ya existe")
    doc = payload.model_dump()
    doc["code"] = code
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    doc["uses_count"] = 0
    await db.coupons.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.delete("/coupons/{coupon_id}")
async def delete_coupon(coupon_id: str, _: dict = Depends(require_admin)):
    res = await db.coupons.delete_one({"id": coupon_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cupón no encontrado")
    return {"message": "Cupón eliminado"}

@api_router.post("/coupons/validate")
async def validate_coupon(payload: CouponValidateRequest):
    code = payload.code.strip().upper()
    coupon = await db.coupons.find_one({"code": code}, {"_id": 0})
    if not coupon or not coupon.get("active", True):
        raise HTTPException(status_code=404, detail="Cupón no válido")
    max_uses = coupon.get("max_uses", 0)
    if max_uses and coupon.get("uses_count", 0) >= max_uses:
        raise HTTPException(status_code=400, detail="Cupón agotado")
    return {
        "code": coupon["code"],
        "discount_percent": coupon["discount_percent"],
    }

app.include_router(api_router)

# CORS
frontend_url = os.environ.get("FRONTEND_URL", "*")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[frontend_url, "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Seed ----------
SAMPLE_PRODUCTS = [
    {
        "title": "Sistema de Trabajos Avanzado",
        "description": "Sistema completo de trabajos legales e ilegales para FiveM con UI moderna, NUI nativa y +20 trabajos preconfigurados. Compatible con QBCore y ESX.",
        "price": 450.0,
        "category": "Scripts",
        "image_url": "https://images.unsplash.com/photo-1742072594013-c87f855e29ca?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjY2NzN8MHwxfHNlYXJjaHwzfHxjb2RlJTIwb24lMjBzY3JlZW4lMjBncmVlbnxlbnwwfHx8fDE3NzcxNjAzMjF8MA&ixlib=rb-4.1.0&q=85",
        "features": ["+20 trabajos preconfigurados", "Compatible QBCore/ESX", "UI NUI moderna", "Soporte técnico 30 días"],
        "badge": "BESTSELLER",
        "gallery": [
            "https://images.unsplash.com/photo-1742072594013-c87f855e29ca?w=940",
            "https://images.unsplash.com/photo-1674159057061-394f68e750a5?w=940",
            "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?w=940",
        ],
        "video_url": "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "views": 142,
    },
    {
        "title": "Base Server QBCore Optimizada",
        "description": "Base lista para producción con +150 recursos premium configurados, anticheat integrado, sistema de housing y economía balanceada.",
        "price": 1800.0,
        "category": "Bases",
        "image_url": "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
        "features": ["+150 recursos premium", "Anticheat integrado", "Sistema housing", "Economía balanceada"],
        "badge": "TOP",
        "gallery": [
            "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?w=940",
            "https://images.unsplash.com/photo-1759926953612-e48779f26629?w=940",
        ],
        "video_url": None,
        "views": 198,
    },
    {
        "title": "Pack de Vehículos Tuning x50",
        "description": "Colección de 50 vehículos custom de alta calidad con tuning completo, hands optimizados y ymt configurados. 4K textures.",
        "price": 650.0,
        "category": "Vehículos",
        "image_url": "https://images.unsplash.com/photo-1759926953612-e48779f26629?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1OTN8MHwxfHNlYXJjaHwxfHxuZW9uJTIwc3BvcnRzJTIwY2FyJTIwZGFya3xlbnwwfHx8fDE3NzcxNjAzMjF8MA&ixlib=rb-4.1.0&q=85",
        "features": ["50 vehículos custom", "Tuning completo", "4K textures", "Hands optimizados"],
        "badge": None,
        "gallery": [
            "https://images.unsplash.com/photo-1759926953612-e48779f26629?w=940",
            "https://images.unsplash.com/photo-1677522375375-c035e66971b3?w=940",
        ],
        "video_url": None,
        "views": 87,
    },
    {
        "title": "MLO Comisaría Premium",
        "description": "Interior completo de comisaría policial con celdas, armería, oficinas, sala de descanso y heliport. Optimizado para 64+ jugadores.",
        "price": 850.0,
        "category": "MLOs",
        "image_url": "https://images.unsplash.com/photo-1677522375375-c035e66971b3?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1OTN8MHwxfHNlYXJjaHwzfHxuZW9uJTIwc3BvcnRzJTIwY2FyJTIwZGFya3xlbnwwfHx8fDE3NzcxNjAzMjF8MA&ixlib=rb-4.1.0&q=85",
        "features": ["Interior completo", "Optimizado 64+ players", "Sin colisiones bug", "Ymap incluido"],
        "badge": "NUEVO",
        "gallery": [
            "https://images.unsplash.com/photo-1677522375375-c035e66971b3?w=940",
            "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?w=940",
        ],
        "video_url": None,
        "views": 53,
    },
    {
        "title": "Sistema de Inventario Hexagonal",
        "description": "Inventario tipo grid hexagonal con drag & drop, hotkeys, peso real, contenedores e integración con todos los principales scripts.",
        "price": 380.0,
        "category": "Scripts",
        "image_url": "https://images.unsplash.com/photo-1674159057061-394f68e750a5?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjY2NzN8MHwxfHNlYXJjaHwxfHxjeWJlcnB1bmslMjBuaWdodCUyMGNpdHl8ZW58MHx8fHwxNzc3MTYwMzIxfDA&ixlib=rb-4.1.0&q=85",
        "features": ["Grid hexagonal único", "Drag & drop fluido", "Sistema de peso", "Hotkeys configurables"],
        "badge": None,
        "gallery": [
            "https://images.unsplash.com/photo-1674159057061-394f68e750a5?w=940",
            "https://images.unsplash.com/photo-1742072594013-c87f855e29ca?w=940",
        ],
        "video_url": None,
        "views": 121,
    },
    {
        "title": "MLO Casino & Hotel Diamond",
        "description": "Mega complejo MLO con casino, hotel de lujo, restaurantes y rooftop. Animaciones interactivas y props sincronizados.",
        "price": 1200.0,
        "category": "MLOs",
        "image_url": "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
        "features": ["Mega complejo", "Animaciones interactivas", "Props sincronizados", "Optimizado para HQ"],
        "badge": "PREMIUM",
        "gallery": [
            "https://images.pexels.com/photos/17195067/pexels-photo-17195067.jpeg?w=940",
            "https://images.unsplash.com/photo-1759926953612-e48779f26629?w=940",
            "https://images.unsplash.com/photo-1674159057061-394f68e750a5?w=940",
        ],
        "video_url": None,
        "views": 165,
    },
]

@app.on_event("startup")
async def startup_event():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.products.create_index("id", unique=True)
    await db.coupons.create_index("code", unique=True)

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@zemdev.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "ZemDev2026!")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin ZemDev",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Admin sembrado: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}},
        )
        logger.info(f"Contraseña admin actualizada: {admin_email}")

    # Seed products (idempotent: only seed when empty)
    count = await db.products.count_documents({})
    if count == 0:
        for p in SAMPLE_PRODUCTS:
            doc = dict(p)
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = datetime.now(timezone.utc).isoformat()
            doc.setdefault("views", 0)
            await db.products.insert_one(doc)
        logger.info(f"Productos sembrados: {len(SAMPLE_PRODUCTS)}")
    else:
        # Backfill new fields on existing products
        await db.products.update_many(
            {"views": {"$exists": False}},
            {"$set": {"views": 0, "gallery": [], "video_url": None}},
        )

    # Seed sample coupon
    if await db.coupons.count_documents({}) == 0:
        await db.coupons.insert_one({
            "id": str(uuid.uuid4()),
            "code": "ZEMDEV10",
            "discount_percent": 10.0,
            "active": True,
            "max_uses": 0,
            "uses_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Cupón sembrado: ZEMDEV10")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
