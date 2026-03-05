"""CLI command to create and bind a Telegram topic from the command line."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ..config import ConfigError, HOME_CONFIG_PATH, load_or_init_config, write_config
from ..config_migrations import migrate_config
from ..engines import list_backend_ids
from ..ids import RESERVED_CHAT_COMMANDS, RESERVED_CLI_COMMANDS
from ..settings import load_settings, validate_settings_data
from ..telegram.client import TelegramClient
from ..telegram.topic_state import TopicStateStore, resolve_state_path
from ..context import RunContext


from ..utils.git import resolve_default_base, resolve_main_worktree_root


def _get_current_branch(cwd: Path) -> str | None:
    """Get current git branch name."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch else None
    except FileNotFoundError:
        pass
    return None


def _get_project_root(cwd: Path) -> Path:
    """Get git project root, handling worktrees."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            common_dir = result.stdout.strip()
            if common_dir:
                # Check if bare repo
                bare_result = subprocess.run(
                    ["git", "rev-parse", "--is-bare-repository"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if bare_result.stdout.strip() == "true":
                    return cwd
                return Path(common_dir).parent
    except FileNotFoundError:
        pass
    return cwd


def _check_alias_conflict(alias: str) -> str | None:
    """Check if project alias conflicts with engine IDs or reserved commands.
    
    Returns conflict reason if conflicts, None otherwise.
    """
    reserved = RESERVED_CLI_COMMANDS | RESERVED_CHAT_COMMANDS
    engine_ids = set(list_backend_ids())
    
    alias_lower = alias.lower()
    if alias_lower in engine_ids:
        return f"engine ID '{alias_lower}'"
    if alias_lower in reserved:
        return f"reserved command '{alias_lower}'"
    return None


def _generate_topic_title(project: str, branch: str | None) -> str:
    """Generate topic title like 'project @branch'."""
    if branch:
        return f"{project} @{branch}"
    return project


async def _create_topic(
    *,
    bot_token: str,
    chat_id: int,
    project: str,
    branch: str | None,
    config_path: Path,
    title_branch: str | None = None,
    system_prompt: str | None = None,
) -> tuple[int, str] | None:
    """Create forum topic and update state file.
    
    Returns (thread_id, title) on success, None on failure.
    ``title_branch`` is only used for the topic title when the user
    explicitly passed ``--branch``.  When *None*, the title is just
    the project alias.
    """
    title = _generate_topic_title(project, title_branch)
    
    client = TelegramClient(bot_token)
    try:
        # Create the forum topic
        result = await client.create_forum_topic(chat_id, title)
        if result is None:
            return None
        
        thread_id = result.message_thread_id
        
        # Update state file
        state_path = resolve_state_path(config_path)
        store = TopicStateStore(state_path)
        
        context = RunContext(project=project.lower(), branch=branch)
        await store.set_context(
            chat_id, thread_id, context,
            topic_title=title,
            system_prompt=system_prompt,
        )
        
        # Send confirmation message to the new topic
        bound_text = f"topic bound to `{project}"
        if branch:
            bound_text += f" @{branch}"
        bound_text += "`"
        if system_prompt:
            bound_text += f"\nsystem prompt: `{system_prompt}`"
        
        await client.send_message(
            chat_id=chat_id,
            text=bound_text,
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        
        return thread_id, title
    finally:
        await client.close()


async def _delete_topic(
    *,
    bot_token: str,
    chat_id: int,
    project: str,
    branch: str | None,
    config_path: Path,
) -> bool:
    """Delete topic binding from state file.
    
    Note: Telegram API doesn't support deleting forum topics, only closing them.
    We remove the binding from state file so Takopi won't recognize it.
    """
    state_path = resolve_state_path(config_path)
    store = TopicStateStore(state_path)
    
    # Find thread by context
    context = RunContext(project=project.lower(), branch=branch)
    thread_id = await store.find_thread_for_context(chat_id, context)
    
    if thread_id is None:
        return False
    
    # Delete from state
    await store.delete_thread(chat_id, thread_id)
    
    # Try to close the topic via API (best effort)
    client = TelegramClient(bot_token)
    try:
        # Note: There's no deleteForumTopic, but we can try to close it
        # or at least send a message indicating it's been unbound
        await client.send_message(
            chat_id=chat_id,
            text=f"topic unbound from `{project}{' @' + branch if branch else ''}`",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
    except Exception:
        pass
    finally:
        await client.close()
    
    return True


def _ensure_project(
    project: str,
    project_root: Path,
    config_path: Path,
) -> None:
    """Ensure project is registered in config, auto-init if needed."""
    config, cfg_path = load_or_init_config()
    
    if cfg_path.exists():
        applied = migrate_config(config, config_path=cfg_path)
        if applied:
            write_config(config, cfg_path)
    
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError(f"Invalid `projects` in {cfg_path}; expected a table.")
    
    # Check if project already exists
    if project in projects:
        return
    
    # Auto-init project
    worktree_base = resolve_default_base(project_root)
    
    entry: dict[str, object] = {
        "path": str(project_root),
        "worktrees_dir": ".worktrees",
    }
    if worktree_base:
        entry["worktree_base"] = worktree_base
    
    projects[project] = entry
    write_config(config, cfg_path)
    typer.echo(f"auto-registered project '{project}'")


def run_topic(
    *,
    project: str | None,
    branch: str | None,
    branch_explicit: bool = False,
    delete: bool,
    config_path: Path | None,
    system_prompt: str | None = None,
) -> None:
    """Create or delete a Telegram topic bound to project/branch."""
    cwd = Path.cwd()
    
    # Resolve project root
    project_root = _get_project_root(cwd)
    
    # Load settings first to check existing projects
    cfg_path = config_path or HOME_CONFIG_PATH
    try:
        settings, cfg_path = load_settings(cfg_path)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    
    # Default project name from directory
    if project is None:
        project = project_root.name.lower()
        if project.endswith(".git"):
            project = project[:-4]
    
    project_key = project.lower()
    
    # Check if project already exists in config
    project_exists = project_key in settings.projects or project in settings.projects
    
    # Check for alias conflicts only for NEW projects
    if not project_exists:
        conflict_reason = _check_alias_conflict(project)
        if conflict_reason:
            typer.echo(
                f"error: project alias '{project}' conflicts with {conflict_reason}.\n"
                f"please specify a different alias: yee88 topic init <alias>",
                err=True,
            )
            raise typer.Exit(code=1)
    
    # Default branch from current git branch
    if branch is None:
        branch = _get_current_branch(cwd)
    
    # Auto-init project if not exists (only for create mode)
    if not delete and not project_exists:
        try:
            _ensure_project(project_key, project_root, cfg_path)
            # Reload settings after auto-init
            settings, cfg_path = load_settings(cfg_path)
        except ConfigError as e:
            typer.echo(f"warning: failed to auto-init project: {e}", err=True)
    
    # Check project exists in config (use settings.projects directly to avoid validation of ALL projects)
    if project_key not in settings.projects and project not in settings.projects:
        typer.echo(
            f"error: project '{project}' not found in config. "
            f"Run `yee88 init {project}` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    
    # Get telegram config
    if settings.transport != "telegram":
        typer.echo("error: only telegram transport is supported", err=True)
        raise typer.Exit(code=1)
    
    tg = settings.transports.telegram
    bot_token = tg.bot_token
    chat_id = tg.chat_id
    
    # Check topics enabled
    if not tg.topics.enabled:
        typer.echo(
            "error: topics not enabled. "
            "Run `yee88 config set transports.telegram.topics.enabled true`",
            err=True,
        )
        raise typer.Exit(code=1)
    
    typer.echo(f"project: {project}")
    typer.echo(f"branch: {branch or '<none>'}")
    typer.echo(f"chat_id: {chat_id}")
    typer.echo("")
    
    if delete:
        # Delete mode
        typer.echo("deleting topic binding...")
        result = asyncio.run(
            _delete_topic(
                bot_token=bot_token,
                chat_id=chat_id,
                project=project,
                branch=branch,
                config_path=cfg_path,
            )
        )
        
        if not result:
            typer.echo(f"error: no topic found for {project}{' @' + branch if branch else ''}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo(f"deleted topic binding for: {project}{' @' + branch if branch else ''}")
        typer.echo("")
        typer.echo("done! the topic has been unbound from yee88.")
        typer.echo("note: the telegram topic still exists but won't be managed by yee88.")
    else:
        # Create mode
        typer.echo("creating topic...")
        result = asyncio.run(
            _create_topic(
                bot_token=bot_token,
                chat_id=chat_id,
                project=project,
                branch=branch,
                config_path=cfg_path,
                title_branch=branch if branch_explicit else None,
                system_prompt=system_prompt,
            )
        )
        
        if result is None:
            typer.echo("error: failed to create topic", err=True)
            raise typer.Exit(code=1)
        
        thread_id, title = result
        typer.echo(f"created topic: {title} (thread_id: {thread_id})")
        typer.echo("")
        typer.echo("done! check telegram for the new topic.")
