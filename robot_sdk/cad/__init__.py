"""CadQuery CAD generation helpers for robot SDK."""

from .cq_assembly import (
    CadQueryAssemblyResult,
    build_cadquery_assembly,
)
from .bbox import CadBoundingBox, bounding_box_from_cad_object
from .cq_local_solve import (
    CadQueryLocalSubassemblySolveResult,
    solve_local_subassemblies,
    solve_local_subassembly_plan,
)
from .cq_full_solve import (
    CadQueryFullAssemblyFixtureResult,
    solve_full_assembly_fixture,
)
from .cq_parts import (
    CadQueryPart,
    CadQueryPartCatalog,
    build_cadquery_parts,
)
from .export import (
    CadQueryExportResult,
    CadQueryStepPackageExportResult,
    CadQuerySubassemblyExportSpec,
    CadQuerySubassemblyExportResult,
    export_cadquery_assembly,
    export_robot_step_package,
    export_step,
)
from .snapshot import (
    StepSnapshotPackageResult,
    StepSnapshotResult,
    generate_step_package_snapshots,
    generate_step_snapshots,
)
from .source_assembly import (
    SourceAssemblyHelper,
    SourceMateRelation,
    SourceMateTarget,
    SourceStepExportResult,
    export_source_step,
    source_mate_payload,
)
from .source_export import (
    SourceStepPackageExportResult,
    SourceSubassemblyExportResult,
    SourceSubassemblyExportSpec,
    export_source_robot_step_package,
)
from .source_parts import (
    SourcePart,
    SourcePartCatalog,
    build_minimal_source_part_catalog,
    build_source_base_part,
    build_source_joint_part,
    build_source_link_part,
    build_source_routed_link_part,
    build_source_structure_joint_part,
    build_source_tool_flange_part,
)
from .source_robot_builder import (
    SourceRobotAssemblyResult,
    build_source_robot_from_layout,
    build_simple_source_serial_robot,
)

__all__ = [
    "CadQueryAssemblyResult",
    "CadBoundingBox",
    "CadQueryLocalSubassemblySolveResult",
    "CadQueryFullAssemblyFixtureResult",
    "CadQueryExportResult",
    "CadQueryStepPackageExportResult",
    "CadQuerySubassemblyExportSpec",
    "CadQuerySubassemblyExportResult",
    "StepSnapshotPackageResult",
    "StepSnapshotResult",
    "SourceAssemblyHelper",
    "SourceMateRelation",
    "SourceMateTarget",
    "SourceStepExportResult",
    "SourceStepPackageExportResult",
    "SourceSubassemblyExportResult",
    "SourceSubassemblyExportSpec",
    "SourcePart",
    "SourcePartCatalog",
    "SourceRobotAssemblyResult",
    "CadQueryPart",
    "CadQueryPartCatalog",
    "build_cadquery_assembly",
    "bounding_box_from_cad_object",
    "build_cadquery_parts",
    "solve_local_subassemblies",
    "solve_local_subassembly_plan",
    "solve_full_assembly_fixture",
    "export_cadquery_assembly",
    "export_robot_step_package",
    "export_step",
    "generate_step_package_snapshots",
    "generate_step_snapshots",
    "export_source_step",
    "export_source_robot_step_package",
    "source_mate_payload",
    "build_minimal_source_part_catalog",
    "build_source_base_part",
    "build_source_joint_part",
    "build_source_link_part",
    "build_source_routed_link_part",
    "build_source_structure_joint_part",
    "build_source_tool_flange_part",
    "build_source_robot_from_layout",
    "build_simple_source_serial_robot",
]
