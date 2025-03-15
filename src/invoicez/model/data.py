from pydantic import BaseModel


class Rate(BaseModel):
    model_config = {"extra": "allow"}

    training_day: int
    preparation_day: int


class Company(BaseModel):
    model_config = {"extra": "allow"}

    name: str
    siren: str
    address: str
    maximum_days: int = 30


class Training(BaseModel):
    model_config = {"extra": "allow"}

    name: str
    short_name: str
    reference: str
    rate: str
    company: str
