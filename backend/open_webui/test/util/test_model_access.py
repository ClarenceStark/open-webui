from types import SimpleNamespace

from open_webui.utils import models as models_utils


def test_get_filtered_models_keeps_provider_models_without_db_record(monkeypatch):
    monkeypatch.setattr(models_utils, "BYPASS_MODEL_ACCESS_CONTROL", False)
    monkeypatch.setattr(models_utils.Models, "get_models_by_ids", lambda ids, db=None: [])
    monkeypatch.setattr(
        models_utils.Groups, "get_groups_by_member_id", lambda user_id, db=None: []
    )
    monkeypatch.setattr(
        models_utils.AccessGrants,
        "get_accessible_resource_ids",
        lambda **kwargs: set(),
    )

    user = SimpleNamespace(id="user-1", role="user")
    models = [
        {"id": "gpt-5.4", "info": {"meta": {}}},
        {"id": "gpt-5.4-pro", "info": {"meta": {}}},
    ]

    assert models_utils.get_filtered_models(models, user) == models


def test_check_model_access_allows_provider_models_without_db_record(monkeypatch):
    monkeypatch.setattr(models_utils.Models, "get_model_by_id", lambda model_id, db=None: None)

    user = SimpleNamespace(id="user-1", role="user")
    model = {"id": "gpt-5.4", "info": {"meta": {}}}

    assert models_utils.check_model_access(user, model) is None


def test_get_filtered_models_keeps_db_backed_models_private_without_read_access(
    monkeypatch,
):
    monkeypatch.setattr(models_utils, "BYPASS_MODEL_ACCESS_CONTROL", False)
    monkeypatch.setattr(
        models_utils.Models,
        "get_models_by_ids",
        lambda ids, db=None: [SimpleNamespace(id="custom-model", user_id="owner-1")],
    )
    monkeypatch.setattr(
        models_utils.Groups, "get_groups_by_member_id", lambda user_id, db=None: []
    )
    monkeypatch.setattr(
        models_utils.AccessGrants,
        "get_accessible_resource_ids",
        lambda **kwargs: set(),
    )

    user = SimpleNamespace(id="user-1", role="user")
    models = [{"id": "custom-model", "info": {"meta": {}}}]

    assert models_utils.get_filtered_models(models, user) == []
