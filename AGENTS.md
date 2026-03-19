# AGENTS.md

## Project Git Workflow

This repository uses a fork-based customization workflow. Keep the branch roles strict.

- `origin` must point to the personal fork: `https://github.com/ClarenceStark/open-webui`
- `upstream` must point to the official repo: `https://github.com/open-webui/open-webui`
- `main` is the official tracking branch and should track `upstream/main`
- `codex/custom-main` is the customization branch and should track `origin/codex/custom-main`

## Branch Rules

- Do not put product-specific or business-specific changes directly on `main`
- Do not commit custom features, experiments, or local deployment tweaks on `main`
- Use `codex/custom-main` as the default base branch for custom development and deployment
- Create short-lived feature branches from `codex/custom-main` when a task is large enough to isolate
- Merge finished custom work back into `codex/custom-main`

## Official Update Procedure

When pulling official updates, use this sequence:

```bash
cd ~/code/open-webui

git checkout main
git fetch upstream
git merge --ff-only upstream/main
git push origin main

git checkout codex/custom-main
git merge main
```

If a cleaner history is explicitly preferred and the branch is safe to rewrite, the last step may be replaced with:

```bash
git checkout codex/custom-main
git rebase main
```

## Daily Development Procedure

Use this sequence for normal custom work:

```bash
cd ~/code/open-webui
git checkout codex/custom-main
git pull --ff-only origin codex/custom-main
```

For a separate task branch:

```bash
git checkout -b codex/<task-name>
```

After finishing the task:

```bash
git checkout codex/custom-main
git merge <task-branch>
git push origin codex/custom-main
```

## Safety Rules

- Never change the meaning of `main`; it is reserved for syncing official upstream changes
- Never force-push `main`
- Avoid force-pushing `codex/custom-main` unless a rebase was intentional and the impact is understood
- Before merging official updates into `codex/custom-main`, resolve conflicts there instead of patching around them on `main`
- Keep customization commits focused and small so future upstream merges stay manageable
- If a customization is deployment-only, prefer configuration or wrapper scripts over deep source edits

## Deployment Note

- The actively deployed custom instance should run from `codex/custom-main`, not from `main`
- After any frontend code change, always run a fresh frontend production build before handing off the task
- Treat frontend work as incomplete until the updated `build/` output has been regenerated
