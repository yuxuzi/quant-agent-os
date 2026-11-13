"""`PytemplateUvScaffolder`: the `ScaffoldProvider` backed by the pytemplate-uv library.

pytemplate-uv is a third-party dependency (``pytemplate-uv>=3.1,<4``). This module is the only
place that imports it, and it uses only its supported interface, ``pytemplate.api``. Rendering
runs in-process with Copier's ``unsafe=False`` and needs no network for the bundled profiles or
this package's local template pack.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from pytemplate import api

from quant_app_factory.domain.ports import (
    ComponentInfo,
    ProfileInfo,
    QuestionInfo,
    ScaffoldPlan,
    ScaffoldRenderError,
    ScaffoldRequest,
    ScaffoldRequestError,
    ScaffoldResult,
    SettingInfo,
    TemplateDescription,
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
    """Return the pytemplate-uv request for ``request``.

    Offered ``defaults`` (such as author and license) are kept only where the profile or its
    resolved components ask for them; explicit ``data`` is passed as given, so pytemplate-uv
    reports an answer that does not apply.
    """
    asked = _asked(request) if request.defaults else set()
    offered = {name: value for name, value in request.defaults.items() if name in asked}
    return api.RenderRequest(
        profile_id=request.profile,
        project_name=request.project_name,
        output_dir=Path(request.output_dir),
        components=tuple(sorted(request.components)),
        no_input=True,
        data={**offered, **request.data},
        template=request.template,
    )


def _asked(request: ScaffoldRequest) -> set[str]:
    """Return the questions the requested profile and its resolved components ask."""
    with api.open_catalog(request.template) as (catalog, _, _):
        profile = catalog.profile(request.profile)
        available = {component.identifier: component for component in catalog.components}
        resolution = api.resolve(profile, tuple(sorted(request.components)), available)
    return {*profile.questions, *(q for c in resolution.components for q in c.questions)}


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

    def describe(self, template: str | None) -> TemplateDescription:
        """Describe a pack directory, or pytemplate-uv's bundled profiles when ``None``."""
        try:
            with api.open_catalog(template) as (catalog, _, _):
                return TemplateDescription(
                    profiles=[
                        ProfileInfo(
                            id=profile.identifier,
                            name=profile.name,
                            description=profile.description,
                            default_components=list(profile.default_components),
                            optional_components=list(profile.optional_components),
                            questions=[q for q in profile.questions if q not in _SET_BY_FACTORY],
                        )
                        for profile in catalog.profiles
                    ],
                    components=[_component(component) for component in catalog.components],
                    questions=[
                        _question(name, spec)
                        for name, spec in catalog.questions.items()
                        if name not in _SET_BY_FACTORY
                    ],
                )
        except _INVALID_REQUEST as error:
            raise ScaffoldRequestError(
                f"pytemplate-uv cannot read the template: {error}"
            ) from error

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
            settings=[_setting(setting) for setting in resolution.settings],
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


# Questions App Factory answers itself: the slug, its package name, the profile, and components.
_SET_BY_FACTORY = frozenset({"project_name", "package_name", "profile", "components"})


def _component(component: api.Component) -> ComponentInfo:
    return ComponentInfo(
        id=component.identifier,
        name=component.name,
        description=component.description,
        compatible_profiles=list(component.compatible_profiles),
        requires=list(component.requires),
        requires_any=list(component.requires_any),
        conflicts=list(component.conflicts),
        questions=list(component.questions),
        settings=[_setting(setting) for setting in component.settings],
    )


def _setting(setting: api.Setting) -> SettingInfo:
    return SettingInfo(
        name=setting.name,
        type=setting.type,
        description=setting.description,
        secret=setting.secret,
        required_in_production=setting.required_in_production,
    )


def _question(name: str, spec: Any) -> QuestionInfo:
    data = spec if isinstance(spec, dict) else {}
    choices = data.get("choices") or []
    default = data.get("default")
    when = data.get("when")
    return QuestionInfo(
        name=name,
        type=str(data.get("type", "str")),
        help=str(data.get("help") or ""),
        choices=[
            str(choice) for choice in (choices.values() if isinstance(choices, dict) else choices)
        ],
        default=None if default is None else str(default),
        when=None if when is None else str(when),
        multiselect=bool(data.get("multiselect", False)),
        secret=bool(data.get("secret", False)),
    )
