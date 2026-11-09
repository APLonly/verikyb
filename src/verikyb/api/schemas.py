from typing import Literal

from verikyb.models import DomainModel


class HealthResponse(DomainModel):
    status: Literal["ok"]
    version: str
