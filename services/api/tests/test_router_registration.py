"""Router registration: planned-but-missing routers are skipped, broken ones fail.

``app.main.V1_ROUTER_MODULES`` lists routers that later features will add
(docs/plan.md §2.3), so a missing router module must be skipped. A router that
exists but has a failing import must still raise, or its endpoints would
vanish without an error.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

from app.main import V1_ROUTER_MODULES, _register_v1_routers, create_app

PKG = "w0_router_probe"


@pytest.fixture()
def probe_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    pkg = tmp_path / PKG
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good_router.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter(prefix="/probe")


            @router.get("/ping")
            def ping() -> dict[str, str]:
                return {"ok": "yes"}
            """
        )
    )
    (pkg / "broken_router.py").write_text("import w0_dependency_that_does_not_exist\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield PKG
    for name in [m for m in sys.modules if m == PKG or m.startswith(f"{PKG}.")]:
        del sys.modules[name]


def _paths(router: APIRouter) -> set[str]:
    """Public paths of ``router``, read from the OpenAPI document of an app that includes it."""

    app = FastAPI()
    app.include_router(router)
    return set(app.openapi()["paths"])


def test_existing_router_is_included(probe_package: str) -> None:
    v1 = APIRouter(prefix="/api/v1")
    _register_v1_routers(v1, (f"{probe_package}.good_router",))
    assert "/api/v1/probe/ping" in _paths(v1)


def test_missing_router_module_or_package_is_skipped(probe_package: str) -> None:
    v1 = APIRouter(prefix="/api/v1")
    _register_v1_routers(
        v1,
        (
            f"{probe_package}.not_built_yet",  # module missing, package exists
            "w0_package_not_built_yet.router",  # whole package missing
            f"{probe_package}.good_router",
        ),
    )
    assert _paths(v1) == {"/api/v1/probe/ping"}


def test_router_with_failing_import_raises(probe_package: str) -> None:
    v1 = APIRouter(prefix="/api/v1")
    with pytest.raises(ModuleNotFoundError, match="w0_dependency_that_does_not_exist"):
        _register_v1_routers(v1, (f"{probe_package}.broken_router",))


def test_planned_routers_are_listed() -> None:
    for module_path in (
        "app.modules.stations.router",
        "app.modules.trips.router",
        "app.modules.trips.observed_router",
        "app.modules.analytics.router",
    ):
        assert module_path in V1_ROUTER_MODULES


def test_app_still_registers_existing_routers() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/api/v1/me" in paths
    assert "/api/v1/routes/directions" in paths
    assert "/api/v1/places/suggest" in paths
