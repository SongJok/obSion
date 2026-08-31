from typing import cast

from fastapi import Request

from obsion.actions.gateway import ActionGateway
from obsion.application.im_delivery import ImDeliveryService
from obsion.application.im_identity import ImIdentityService
from obsion.application.workspaces import WorkspaceService
from obsion.capabilities.connector_spi import ConnectorSdkRuntime
from obsion.capabilities.gateway import CapabilityGateway


def get_workspace_service(request: Request) -> WorkspaceService:
    return cast(WorkspaceService, request.app.state.workspace_service)


def get_im_identity_service(request: Request) -> ImIdentityService:
    return cast(ImIdentityService, request.app.state.im_identity_service)


def get_im_delivery_service(request: Request) -> ImDeliveryService:
    return cast(ImDeliveryService, request.app.state.im_delivery_service)


def get_capability_gateway(request: Request) -> CapabilityGateway:
    return cast(CapabilityGateway, request.app.state.capability_gateway)


def get_action_gateway(request: Request) -> ActionGateway:
    return cast(ActionGateway, request.app.state.action_gateway)


def get_connector_sdk_runtime(request: Request) -> ConnectorSdkRuntime:
    return cast(ConnectorSdkRuntime, request.app.state.connector_sdk_runtime)
