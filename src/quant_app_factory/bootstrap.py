"""Wire the App Factory to its settings file and the pytemplate-uv scaffolder."""

from __future__ import annotations

from quant_app_factory.application.factory import AppFactory
from quant_app_factory.domain.ports import ScaffoldError
from quant_app_factory.infrastructure.settings_file import git_identity, load_settings

INSTALL_HINT = "install the quant-agent-os[factory] extra"


def open_factory() -> AppFactory:
    """Return the factory the settings file configures.

    pytemplate-uv is imported here, not at the top of a module, so `qaos` starts without the
    ``factory`` extra and does not load Copier until something needs it.
    """
    settings = load_settings()
    try:
        from quant_app_factory.infrastructure.pytemplate_uv import PytemplateUvScaffolder
    except ImportError as error:
        raise ScaffoldError(f"The App Factory is not installed; {INSTALL_HINT}.") from error
    return AppFactory(
        PytemplateUvScaffolder(),
        identity=lambda: settings.identity.merged(git_identity()),
        connections=lambda: settings.connections,
        templates=lambda: {name: entry.path for name, entry in settings.templates.items()},
    )
