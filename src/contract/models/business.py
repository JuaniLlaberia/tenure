from pydantic import BaseModel

class BusinessProfile(BaseModel):
    business_id: str
    name: str
    what_you_sell: str
    customers: str
    prices: str | None = None
    tone: str | None = None
    main_clients: list[str] = []
    links: list[str] = []
    extra: dict[str, str] = {}
