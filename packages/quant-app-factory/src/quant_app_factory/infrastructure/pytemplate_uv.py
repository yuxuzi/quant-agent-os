"""`PytemplateUvScaffolder`: the `ScaffoldProvider` backed by the pytemplate-uv library.

pytemplate-uv is a third-party dependency (``pytemplate-uv>=3.0,<4``). This module is the only
place that imports it, and it uses only its supported interface, ``pytemplate.api``. Rendering
runs in-process with Copier's ``unsafe=False`` and needs no network for the bundled profiles or
this package's local template pack.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pytemplate import api

from quant_app_factory.domain.ports import (
    ScaffoldPlan,
    ScaffoldRenderError,
    ScaffoldRequest,
    ScaffoldRequestError,
    ScaffoldResult,
)

# Errors meaning the request is invalid and nothing was written (pytemplate-uv exit code 1).
_INVALID_REQUEST = (
    api.RequestError,
    api.ConfigurationError,
    api.ManifestError,
    api.ProfileNotFoundError,
    api.ComponentNotFoundError,
    api.ComponentCompatibilityError,
)


def _render_request(request: ScaffoldRequest) -> api.RenderRequest:
    """Return the pytemplate-uv request for ``request``."""
    return api.RenderRequest(
        profile_id=request.profile,
        project_name=request.project_name,
        output_dir=Path(request.output_dir),
        components=tuple(sorted(request.components)),
        no_input=True,
        data=dict(request.data),
        template=request.template,
    )


def _source(source: api.TemplateSource) -> dict[str, str | None]:
    return {
        "kind": source.kind,
        "location": source.location,
        "ref": source.ref,
        "pack_id": source.pack_id,
        "pack_version": source.pack_version,
    }


class PytemplateUvScaffolder:
    """Plan and create projects with pytemplate-uv 3."""

    name = "pytemplate-uv"

    @property
    def version(self) -> str:
        """The installed pytemplate-uv, as ``pytemplate-uv==<version>``."""
        try:
            return f"pytemplate-uv=={version('pytemplate-uv')}"
        except PackageNotFoundError:  # pragma: no cover - always installed with this package
            return "pytemplate-uv"

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan:
        """Return the exact files, dependencies, and checks. Nothing is written to the target."""
        try:
            planned = api.plan(_render_request(request))
        except _INVALID_REQUEST as error:
            raise ScaffoldRequestError(f"pytemplate-uv rejected the request: {error}") from error
        except api.RenderError as error:
            raise ScaffoldRenderError(
                f"pytemplate-uv could not plan the project: {error}"
            ) from error
        resolution = planned.resolution
        return ScaffoldPlan(
            project_path=str(planned.project_path),
            target_exists=planned.target_exists,
            package_name=planned.package_name,
            profile=resolution.profile.identifier,
            profile_version=resolution.profile.version,
            components=list(resolution.component_ids),
            dependencies=list(resolution.dependencies),
            dependency_groups={
                group: list(specs) for group, specs in resolution.dependency_groups.items()
            },
            lifecycle_commands=list(resolution.lifecycle_commands),
            files=list(planned.files),
            template=_source(planned.source),
            security_notes=list(resolution.security_notes),
        )

    def create(self, request: ScaffoldRequest) -> ScaffoldResult:
        """Render and publish the project; pytemplate-uv publishes it only if rendering succeeds."""
        try:
            result = api.render(_render_request(request))
        except _INVALID_REQUEST as error:
            raise ScaffoldRequestError(f"pytemplate-uv rejected the request: {error}") from error
        except api.RenderError as error:
            raise ScaffoldRenderError(f"pytemplate-uv failed while rendering: {error}") from error
        return ScaffoldResult(
            project_path=str(result.project_path),
            manifest_path=str(result.manifest_path) if result.manifest_path else None,
            profile=result.profile.identifier,
            profile_version=result.profile.version,
            components=[component.identifier for component in result.components],
            lifecycle_commands=list(result.resolution.lifecycle_commands),
        )
