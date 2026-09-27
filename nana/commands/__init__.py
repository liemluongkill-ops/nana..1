"""nana.commands — command routing subsystem."""

from nana.commands.registry import (
    KNOWN_SLASH_COMMANDS,
    OSU_STATIC_COMMANDS,
    COMMAND_NORMALIZATION_CASES,
    COMMAND_ROUTE_CASES,
    STARDEW_STATIC_COMMANDS,
)
from nana.commands.router import (
    normalize_command_text,
    suggest_slash_command,
    command_joined_issue,
    command_route_analysis,
    command_route_regression_rows,
    command_normalize_regression_rows,
)
from nana.commands.router_manifest import (
    classify_command_truth,
    command_truth_lines,
    command_truth_status_lines,
)
from nana.commands.registry_truth import (
    classify_registry_commands,
    registry_truth_summary,
    registry_truth_status_lines,
)
from nana.commands.dispatch_surface import (
    DispatchSurface,
    dispatch_surface_status_lines,
    is_static_dispatch_command,
    static_dispatch_surface,
)
from nana.commands.help import (
    print_command_help,
    print_phase3_state,
    print_phase4_state,
)
