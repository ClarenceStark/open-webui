from pathlib import Path

from open_webui.utils.misc import API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT


def build_codex_browser_runtime_prompt(workspace: Path) -> str:
    output_dir = workspace / "outputs" / "screenshots"
    lines = [
        "When this Open WebUI Codex session needs webpage screenshots, use Node.js with the installed `playwright` package in headless Chromium mode.",
        "Do not use Browser Use, an in-app browser, macOS `open`, or the user's regular Google Chrome application/profile for screenshot work.",
        "Create any browser context from the current workspace and save screenshots inside the current Codex workspace.",
        f"Prefer this screenshot output directory when practical: {output_dir}",
    ]
    return "\n".join(lines)


def build_codex_developer_instructions(workspace: Path) -> str:
    return "\n\n".join(
        [
            API_KEY_NON_DISCLOSURE_SYSTEM_PROMPT,
            build_codex_browser_runtime_prompt(workspace),
        ]
    )
