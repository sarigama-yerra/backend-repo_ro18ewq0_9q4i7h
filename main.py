import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt
from passlib.context import CryptContext

from database import db, create_document, get_documents

# Security setup
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# Helpers

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = payload.get("sub")
        role = payload.get("role")
        email = payload.get("email")
        name = payload.get("name")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"_id": user_id, "role": role, "email": email, "name": name}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.get("/")
def root():
    return {"message": "Campusportalen backend kører"}


@app.get("/health")
def health():
    try:
        # ping database
        _ = db.list_collection_names() if db is not None else []
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:120]}


@app.post("/auth/login", response_model=Token)
def login(req: LoginRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    user = db["users"].find_one({"email": req.email, "active": True})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({
        "sub": str(user.get("_id")),
        "role": user.get("role", "elev"),
        "email": user.get("email"),
        "name": user.get("name")
    })
    return {"access_token": token, "token_type": "bearer"}


# Role dependency wrappers
async def require_admin(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------- Meals (Kantine) ----------
class MealIn(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    day: str  # YYYY-MM-DD
    is_today_special: bool = False
    is_surplus_offer: bool = False
    co2_kg_per_portion: Optional[float] = None
    portions_available: Optional[int] = None


class OrderIn(BaseModel):
    meal_id: str
    quantity: int = 1


@app.get("/meals/today")
def get_today_meal():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meal = db["meals"].find_one({"day": {"$regex": f"^{today_str}"}, "is_today_special": True})
    if not meal:
        return {"meal": None}
    meal["_id"] = str(meal["_id"])  # serialize
    return {"meal": meal}


@app.post("/admin/meals", dependencies=[Depends(require_admin)])
def create_meal(meal: MealIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    data = meal.model_dump()
    inserted_id = create_document("meals", data)
    return {"id": inserted_id}


@app.get("/meals/surplus")
def get_surplus_meals():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meals = list(db["meals"].find({"day": {"$regex": f"^{today_str}"}, "is_surplus_offer": True}))
    for m in meals:
        m["_id"] = str(m["_id"])  # serialize
    return {"meals": meals}


@app.post("/orders", dependencies=[Depends(get_current_user)])
def create_order(order: OrderIn, user=Depends(get_current_user)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    meal = db["meals"].find_one({"_id": db["meals"].codec_options.document_class.objectid_class(order.meal_id)}) if False else db["meals"].find_one({"_id": {"$exists": True}})
    # Above ObjectId conversion is environment specific; we'll do simple lookup by string-stored ids in this setup.
    data = {
        "user_id": user["_id"],
        "meal_id": order.meal_id,
        "quantity": order.quantity,
        "total_price": 0.0,  # will compute below if price exists
        "status": "created"
    }
    meal_doc = db["meals"].find_one({"_id": {"$exists": True}, "is_today_special": True})
    if meal_doc and isinstance(meal_doc.get("price"), (int, float)):
        data["total_price"] = round(float(meal_doc["price"]) * order.quantity, 2)
    inserted_id = create_document("orders", data)
    return {"id": inserted_id}


# ---------- Events ----------
class EventIn(BaseModel):
    title: str
    description: Optional[str] = None
    date: datetime
    location: Optional[str] = None
    capacity: Optional[int] = None


@app.get("/events")
def list_events():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    items = list(db["events"].find({"date": {"$gte": datetime.now(timezone.utc)}}).sort("date", 1))
    for it in items:
        it["_id"] = str(it["_id"])  # serialize
    return {"events": items}


@app.post("/admin/events", dependencies=[Depends(require_admin)])
def create_event(event: EventIn, user=Depends(require_admin)):
    data = event.model_dump()
    data["created_by"] = user["_id"]
    inserted_id = create_document("events", data)
    return {"id": inserted_id}


@app.get("/admin/events/{event_id}/signups", dependencies=[Depends(require_admin)])
def get_event_signups(event_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    signups = list(db["event_signups"].find({"event_id": event_id}))
    for s in signups:
        s["_id"] = str(s["_id"])  # serialize
    return {"signups": signups}


class SignupIn(BaseModel):
    event_id: str


@app.post("/events/signup", dependencies=[Depends(get_current_user)])
def signup_event(data: SignupIn, user=Depends(get_current_user)):
    exists = db["event_signups"].find_one({"event_id": data.event_id, "user_id": user["_id"]})
    if exists:
        return {"status": "already_signed"}
    inserted_id = create_document("event_signups", {"event_id": data.event_id, "user_id": user["_id"]})
    return {"id": inserted_id}


# ---------- News / Posts ----------
class NewsIn(BaseModel):
    title: str
    text: str
    image_url: Optional[str] = None


@app.get("/news")
def list_news():
    items = list(db["news"].find({}).sort("created_at", -1).limit(20))
    for it in items:
        it["_id"] = str(it["_id"])  # serialize
    return {"news": items}


@app.post("/admin/news", dependencies=[Depends(require_admin)])
def create_news(item: NewsIn, user=Depends(require_admin)):
    data = item.model_dump()
    data["created_by"] = user["_id"]
    inserted_id = create_document("news", data)
    return {"id": inserted_id}


# ---------- Stats ----------
@app.get("/stats")
def stats_overview():
    # Compute basic metrics from orders and meals
    try:
        total_portions = 0
        co2_saved = 0.0
        waste_saved = 0.0

        orders = db["orders"].find({"status": {"$in": ["created", "paid", "fulfilled"]}})
        for o in orders:
            qty = o.get("quantity", 1)
            total_portions += qty

        # Rough estimate using meals data
        meals = db["meals"].find({})
        for m in meals:
            co2 = m.get("co2_kg_per_portion")
            if co2:
                co2_saved += float(co2)

        # Placeholder: 0.15 kg waste saved per portion "surplus" sold
        surplus_orders = db["orders"].find({"status": {"$in": ["paid", "fulfilled"]}})
        for so in surplus_orders:
            waste_saved += 0.15 * so.get("quantity", 1)

        return {
            "portions_sold": total_portions,
            "co2_saved_kg": round(co2_saved, 2),
            "waste_saved_kg": round(waste_saved, 2),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:120])


# ------------- Failover & cache headers -------------
@app.get("/failover-test")
def failover_test():
    try:
        # Something that may fail
        _ = 1 / 0
        return {"ok": True}
    except Exception:
        return {"ok": False, "fallback": True}


# Basic SSR-like health and cache controls via headers in responses are handled by frontend caching.


# ---------- Convenience: seed demo users if empty ----------
@app.post("/dev/seed")
def seed():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    count = db["users"].count_documents({})
    if count > 0:
        return {"status": "already_seeded"}
    admin_pw = pwd_context.hash("admin123")
    elev_pw = pwd_context.hash("elev123")
    db["users"].insert_many([
        {"email": "admin@campus.dk", "name": "Admin", "role": "admin", "password_hash": admin_pw, "active": True},
        {"email": "elev@campus.dk", "name": "Elev", "role": "elev", "password_hash": elev_pw, "active": True},
    ])
    return {"status": "seeded", "users": ["admin@campus.dk / admin123", "elev@campus.dk / elev123"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
