from typing import cast

from fastapi import Request

from obsion.actions.gateway import ActionGateway
from obsion.application.workspaces import WorkspaceService
from obsion.capabilities.gateway import CapabilityGateway


def get_workspace_service(request: Request) -> WorkspaceService:
    return cast(WorkspaceService, request.app.state.workspace_service)


def get_capability_gateway(request: Request) -> CapabilityGateway:
    return cast(CapabilityGateway, request.app.state.capability_gateway)


def get_action_gateway(request: Request) -> ActionGateway:
    return cast(ActionGateway, request.app.state.action_gateway)
