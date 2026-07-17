from open_webui.codex import config


def test_codex_env_uses_danger_full_access_home_for_allowlisted_email(
    tmp_path, monkeypatch
):
    base_home = tmp_path / "codex-home"
    calls = []

    monkeypatch.setattr(config, "CODEX_WEB_HOME", base_home)
    monkeypatch.setattr(
        config,
        "CODEX_DANGER_FULL_ACCESS_EMAILS",
        frozenset({"ykl@gpt.com", "cxh@gpt.com"}),
    )
    monkeypatch.setattr(config, "_node_module_paths", lambda: [])
    monkeypatch.setattr(config, "_DEFAULT_PLAYWRIGHT_CACHE", tmp_path / "missing")

    def fake_bootstrap(codex_home=base_home, sandbox_mode="workspace-write"):
        calls.append((codex_home, sandbox_mode))

    monkeypatch.setattr(config, "_bootstrap_codex_web_home", fake_bootstrap)

    env = config.get_codex_app_server_env("YKL@GPT.COM")

    assert env["CODEX_HOME"] == str(base_home / "users" / "ykl_gpt.com")
    assert calls == [(base_home / "users" / "ykl_gpt.com", "danger-full-access")]
    assert config.get_codex_app_server_config_overrides("YKL@GPT.COM") == (
        'sandbox_mode="danger-full-access"',
        'approval_policy="never"',
    )


def test_codex_env_keeps_workspace_write_for_other_users(tmp_path, monkeypatch):
    base_home = tmp_path / "codex-home"
    calls = []

    monkeypatch.setattr(config, "CODEX_WEB_HOME", base_home)
    monkeypatch.setattr(
        config,
        "CODEX_DANGER_FULL_ACCESS_EMAILS",
        frozenset({"ykl@gpt.com", "cxh@gpt.com"}),
    )
    monkeypatch.setattr(config, "_node_module_paths", lambda: [])
    monkeypatch.setattr(config, "_DEFAULT_PLAYWRIGHT_CACHE", tmp_path / "missing")

    def fake_bootstrap(codex_home=base_home, sandbox_mode="workspace-write"):
        calls.append((codex_home, sandbox_mode))

    monkeypatch.setattr(config, "_bootstrap_codex_web_home", fake_bootstrap)

    env = config.get_codex_app_server_env("someone@example.com")

    assert env["CODEX_HOME"] == str(base_home)
    assert calls == [(base_home, "workspace-write")]
    assert config.get_codex_app_server_config_overrides("someone@example.com") == ()


def test_model_override_maps_legacy_codex_model_to_gpt_5_5():
    assert config.get_model_override("gpt-5.5") == "gpt-5.5"
    assert config.get_model_override("gpt-5.4") == "gpt-5.5"
    assert config.get_model_override("codex/gpt-5.4") == "gpt-5.5"


def test_bootstrap_migrates_legacy_profile_config(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setattr(config, "CODEX_WEB_HOME", codex_home)
    monkeypatch.setattr(config, "_NODE_SHIMS_DIR", codex_home / "node-shims")
    monkeypatch.setattr(config, "_CODEX_WEB_SOURCE_CONFIG", tmp_path / "missing.toml")
    monkeypatch.setattr(config, "_DEFAULT_CODEX_HOME", tmp_path / "default-codex")
    monkeypatch.setattr(config, "_DEFAULT_AGENTS_HOME", tmp_path / "default-agents")
    monkeypatch.setattr(config, "_DEFAULT_PLAYWRIGHT_CACHE", tmp_path / "missing")
    monkeypatch.setattr(config, "_node_module_paths", lambda: [])
    monkeypatch.setattr(config, "_sandbox_temp_paths", lambda: [])
    monkeypatch.setattr(config, "_write_playwright_shims", lambda: None)

    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                'profile = "azure"',
                'model_provider = "azure"',
                "",
                "[profiles.azure]",
                'model_provider = "azure"',
                'model = "gpt-5.5"',
                'model_reasoning_effort = "xhigh"',
                "",
                "[profiles.chatgpt]",
                'model_provider = "openai"',
                'model = "gpt-5.5"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config._bootstrap_codex_web_home(codex_home, "danger-full-access")

    config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
    azure_text = (codex_home / "azure.config.toml").read_text(encoding="utf-8")
    chatgpt_text = (codex_home / "chatgpt.config.toml").read_text(encoding="utf-8")

    assert 'profile = "azure"' not in config_text
    assert "[profiles.azure]" not in config_text
    assert 'model_provider = "azure"' in azure_text
    assert 'model = "gpt-5.5"' in azure_text
    assert 'model_reasoning_effort = "xhigh"' in azure_text
    assert 'model_provider = "openai"' in chatgpt_text


def test_bootstrap_flattens_profile_scalars_for_app_server(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setattr(config, "CODEX_WEB_PROFILE", "azure")
    monkeypatch.setattr(config, "CODEX_WEB_HOME", codex_home)
    monkeypatch.setattr(config, "_NODE_SHIMS_DIR", codex_home / "node-shims")
    monkeypatch.setattr(config, "_CODEX_WEB_SOURCE_CONFIG", tmp_path / "missing.toml")
    monkeypatch.setattr(config, "_DEFAULT_CODEX_HOME", tmp_path / "default-codex")
    monkeypatch.setattr(config, "_DEFAULT_AGENTS_HOME", tmp_path / "default-agents")
    monkeypatch.setattr(config, "_DEFAULT_PLAYWRIGHT_CACHE", tmp_path / "missing")
    monkeypatch.setattr(config, "_node_module_paths", lambda: [])
    monkeypatch.setattr(config, "_sandbox_temp_paths", lambda: [])
    monkeypatch.setattr(config, "_write_playwright_shims", lambda: None)

    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        'model_provider = "openai"\nmodel = "legacy-model"\n',
        encoding="utf-8",
    )
    (codex_home / "azure.config.toml").write_text(
        'model_provider = "azure"\nmodel = "gpt-5.5"\nmodel_reasoning_effort = "high"\n',
        encoding="utf-8",
    )

    config._bootstrap_codex_web_home(codex_home, "danger-full-access")

    config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model_provider = "azure"' in config_text
    assert 'model = "gpt-5.5"' in config_text
    assert 'model_reasoning_effort = "high"' in config_text
