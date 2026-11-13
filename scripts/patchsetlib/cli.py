"""Command-line interface for the patchset pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import config
from .commands import changelog as changelog_command
from .commands import check as check_command
from .commands import doctor as doctor_command
from .commands import export as export_command
from .commands import hook_pre_commit
from .commands import lint as lint_command
from .commands import rebase as rebase_command
from .commands import setup as setup_command
from .commands import status as status_command
from .errors import PatchsetError
from .log import error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchset.py",
        description="Telegram Desktop patchset pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="create or reuse the base checkout")
    setup.add_argument("--ci", action="store_true", help="disposable exported checkout")
    setup.add_argument("--force", action="store_true", help="rebuild a diverged stack")
    setup.add_argument(
        "--no-submodules", action="store_true", help="skip submodules and deps patches"
    )
    setup.set_defaults(handler=lambda args: setup_command.run(
        ci=args.ci, force=args.force, no_submodules=args.no_submodules
    ))

    export = sub.add_parser("export", help="regenerate patches/ and series")
    export.add_argument("--check", action="store_true", help="compare only")
    export.add_argument(
        "--allow-dirty", action="store_true", help="do not require a clean worktree"
    )
    export.set_defaults(handler=lambda args: export_command.run(
        check_only=args.check, allow_dirty=args.allow_dirty
    ))

    check = sub.add_parser("check", help="run all validation gates")
    check.add_argument("--drop-one", action="store_true", help="advisory dependency audit")
    check.add_argument("--base", help="validate against an in-flight rebase target")
    check.set_defaults(handler=lambda args: check_command.run(
        drop_one=args.drop_one, base_arg=args.base
    ))

    lint = sub.add_parser("lint", help="cross-patch overwrite report")
    lint.add_argument("--check", action="store_true", help="exit 1 on findings")
    lint.set_defaults(handler=lambda args: lint_command.run(check=args.check))

    rebase = sub.add_parser("rebase", help="move the stack to a newer release tag")
    rebase.add_argument("target", help="latest or a 64Gram release tag")
    rebase.add_argument(
        "--allow-non-descendant",
        action="store_true",
        help="allow a deliberate backward move",
    )
    rebase.set_defaults(handler=lambda args: rebase_command.rebase(
        config.worktree_path(), args.target, args.allow_non_descendant
    ))

    status = sub.add_parser("status", help="print stack and rebase state")
    status.set_defaults(handler=lambda args: status_command.run())

    cont = sub.add_parser("continue", help="finish the current conflict")
    cont.set_defaults(handler=lambda args: rebase_command.continue_rebase(
        config.worktree_path()
    ))

    finalize = sub.add_parser("finalize", help="finish a checked rebase")
    finalize.set_defaults(handler=lambda args: rebase_command.finalize(
        config.worktree_path()
    ))

    changelog = sub.add_parser("changelog", help="extract release notes")
    changelog.add_argument("--tag", required=True)
    changelog.add_argument("--check", action="store_true")
    changelog.set_defaults(handler=lambda args: changelog_command.run(
        tag=args.tag, check=args.check
    ))

    doctor = sub.add_parser("doctor", help="print prerequisites")
    doctor.set_defaults(handler=lambda args: doctor_command.run_doctor())

    hook = sub.add_parser("hook-pre-commit", help=argparse.SUPPRESS)
    hook.set_defaults(handler=lambda args: hook_pre_commit.run())

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except BrokenPipeError:
        return 0
    except PatchsetError as exc:
        error(str(exc))
        return exc.exit_code
    return 0
