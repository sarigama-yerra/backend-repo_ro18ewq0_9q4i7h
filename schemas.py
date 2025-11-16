"""
Database Schemas for Campusportalen

Each Pydantic model represents a MongoDB collection in the connected database.
Collection name is the lowercase of the class name by default.

Collections:
- users
- events
- event_signups
- meals
- orders
- news
- stats
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class Users(BaseModel):
    email: str = Field(..., description="Email used for login")
    name: str = Field(..., description="Full name")
    role: str = Field(..., description="Role: admin or elev")
    password_hash: str = Field(..., description="Hashed password (bcrypt)")
    active: bool = Field(True, description="Whether the user can log in")


class Events(BaseModel):
    title: str
    description: Optional[str] = None
    date: datetime
    location: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=0)
    created_by: Optional[str] = Field(None, description="User id (string) of the creator")


class Event_signups(BaseModel):
    event_id: str = Field(..., description="Referenced event _id as string")
    user_id: str = Field(..., description="Referenced user _id as string")
    created_at: Optional[datetime] = None


class Meals(BaseModel):
    name: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    day: datetime = Field(..., description="Date the meal is offered (YYYY-MM-DD)")
    is_today_special: bool = Field(False, description="Mark as dagens ret")
    is_surplus_offer: bool = Field(False, description="Shown as overskudsmad tilbud efter frokost")
    co2_kg_per_portion: Optional[float] = Field(None, ge=0)
    portions_available: Optional[int] = Field(None, ge=0)


class Orders(BaseModel):
    user_id: str
    meal_id: str
    quantity: int = Field(1, ge=1)
    total_price: float = Field(..., ge=0)
    status: str = Field("created", description="created|paid|cancelled|fulfilled")


class News(BaseModel):
    title: str
    text: str
    image_url: Optional[str] = None
    created_by: Optional[str] = None


class Stats(BaseModel):
    # Aggregated snapshots (optional) – we also compute live from orders
    date: datetime
    portions_sold: int = 0
    food_waste_kg_saved: float = 0.0
    co2_kg_saved: float = 0.0
    notes: Optional[str] = None
