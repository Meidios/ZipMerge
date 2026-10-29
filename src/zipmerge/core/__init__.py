"""核心模型：目标树、撤销栈、导入语义、冲突检测、命名模板、导出计划。"""

from .conflict import (
    Conflict,
    ConflictKind,
    Resolution,
    ResolveStrategy,
    detect_conflicts,
    resolve_mappings,
)
from .exporter import (
    ExportCancelled,
    ExportFailure,
    ExportOptions,
    ExportResult,
    build_resolution,
    estimate_export_summary,
    estimate_export_size,
    run_export,
)
from .extraction import (
    ExtractionConflicts,
    ExtractionJob,
    ExtractionPlan,
    ExtractionSelection,
    OverwriteMode,
    build_extraction_plan,
    inspect_extraction_conflicts,
)
from .nodes import NodeKind, SourceRef, TargetNode, validate_name
from .materialize import (
    MaterializeRequest,
    MaterializeResult,
    build_source_drag_root,
    build_target_drag_root,
    materialize_directory,
    top_level_nodes,
)
from .plan import Mapping, build_export_plan
from .session import MergeSession, SourceArchive
from .template import (
    DEFAULT_TEMPLATE,
    TEMPLATE_FIELDS,
    NamingFields,
    render_filename,
    suggest_content_tag,
    unique_output_path,
)
from .undo import UndoStack

__all__ = [
    "NodeKind",
    "SourceRef",
    "TargetNode",
    "validate_name",
    "UndoStack",
    "MergeSession",
    "SourceArchive",
    "Conflict",
    "ConflictKind",
    "Resolution",
    "ResolveStrategy",
    "detect_conflicts",
    "resolve_mappings",
    "Mapping",
    "build_export_plan",
    "ExportOptions",
    "ExportResult",
    "ExportFailure",
    "ExportCancelled",
    "build_resolution",
    "estimate_export_summary",
    "estimate_export_size",
    "run_export",
    "MaterializeRequest",
    "MaterializeResult",
    "materialize_directory",
    "build_source_drag_root",
    "build_target_drag_root",
    "top_level_nodes",
    "ExtractionSelection",
    "ExtractionJob",
    "ExtractionPlan",
    "ExtractionConflicts",
    "OverwriteMode",
    "build_extraction_plan",
    "inspect_extraction_conflicts",
    "NamingFields",
    "DEFAULT_TEMPLATE",
    "TEMPLATE_FIELDS",
    "render_filename",
    "suggest_content_tag",
    "unique_output_path",
]
