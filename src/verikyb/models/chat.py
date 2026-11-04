"""Chat history, kept separately for every request, workspace and tab."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from verikyb.models.common import WORKSPACE_TABS, DomainModel, RequestId, Tab, Workspace


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(DomainModel):
    message_id: str
    request_id: RequestId
    workspace: Workspace
    tab: Tab
    role: ChatRole
    content: str
    evidence_ids: list[str] = []
    user_id: str
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _tab_belongs_to_workspace(self) -> Self:
        if self.tab not in WORKSPACE_TABS[self.workspace]:
            raise ValueError(f"tab {self.tab} is not part of the {self.workspace} workspace")
        return self
