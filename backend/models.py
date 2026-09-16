from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Offer(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    warehouse: str = Field(min_length=1, max_length=200)
    stock: StrictInt = Field(ge=0, le=1000000)
    days: StrictInt = Field(ge=0, le=365)
    price: StrictInt = Field(gt=0, le=100000000)

class Product(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    sku: str = Field(min_length=1, max_length=100)
    kind: Literal['tires','wheels']
    brand: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    image: str = Field(pattern=r'^assets/[a-zA-Z0-9_./-]+\.(png|jpg|jpeg|webp)$')
    isDemo: bool
    diameter: float = Field(ge=10, le=30)
    description: str = Field(default='', max_length=3000)
    rank: int = 0
    width: int | None = Field(default=None, ge=100, le=500)
    profile: int | None = Field(default=None, ge=15, le=100)
    season: Literal['summer','winter','allseason'] | None = None
    loadIndex: str | None = None
    speedIndex: str | None = None
    studded: bool = False
    runflat: bool = False
    xl: bool = False
    wheelWidth: float | None = Field(default=None, ge=3, le=16)
    pcd: str | None = None
    et: float | None = Field(default=None, ge=-100, le=200)
    dia: float | None = Field(default=None, ge=30, le=200)
    type: Literal['alloy','forged','steel'] | None = None
    color: str | None = None
    offers: list[Offer] = Field(max_length=500)

    @model_validator(mode='after')
    def coherent(self):
        required = ['width','profile','season'] if self.kind == 'tires' else ['wheelWidth','pcd','et','dia','type']
        if any(getattr(self,k) is None for k in required):
            raise ValueError('Missing category parameters')
        if '..' in self.image.split('/'):
            raise ValueError('Image path traversal')
        if len({o.id for o in self.offers}) != len(self.offers):
            raise ValueError('Duplicate offer id')
        return self

class Catalog(StrictModel):
    schemaVersion: Literal[1]
    mode: Literal['demo','live']
    updatedAt: datetime
    notice: str = Field(max_length=3000)
    products: list[Product] = Field(min_length=1, max_length=200000)

    @model_validator(mode='after')
    def unique(self):
        if len({p.id for p in self.products}) != len(self.products):
            raise ValueError('Duplicate product id')
        if self.mode == 'live' and any(p.isDemo for p in self.products):
            raise ValueError('Demo products cannot be declared live')
        return self

class QuoteLine(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    quantity: StrictInt = Field(ge=1, le=20)

class QuoteRequest(StrictModel):
    lines: list[QuoteLine] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({l.id for l in self.lines}) != len(self.lines):
            raise ValueError('Duplicate product line')
        return self

def best_offer(product: Product, quantity: int = 1):
    offers = [o for o in product.offers if o.stock >= quantity]
    return min(offers, key=lambda o: (o.days,o.price)) if offers else None
