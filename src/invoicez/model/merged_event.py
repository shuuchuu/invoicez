from datetime import datetime

from pydantic import BaseModel

from .duration import Duration


class MergedEvent(BaseModel):
    model_config = {"use_enum_values": True}
    company: str
    training: str
    place: str
    description: str | None
    link: str
    id: str
    starts: list[datetime]
    durations: list[Duration]

    def __str__(self) -> str:
        return f"<{self.company} ⋅ {self.training} ⋅ {self.place} ⋅ {self.starts[0]}>"
