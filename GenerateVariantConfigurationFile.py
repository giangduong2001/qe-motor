# -*- coding: utf-8 -*-
"""
GenerateVariantConfigurationFile.py

Generate ramp_variant_config.h and ramp_instance_registry.c from a project's FSP
configuration.xml. Runs as an EASE script (Python standard library only).

Ported from scripts/variants/generate_ramp_variant.py (RAMP repository, branch
feature/assisted_tuning). Differences from the original:
  - no jinja2 / yaml: the file templates are plain text files rendered with
    string.Template (placeholders are written ${name})
  - enum symbols and schema versions are read from the project's own firmware headers
    (ramp_variant_model.h, ramp_instance_registry.h) instead of enums.yaml
  - the renderer is selected by the schema MAJOR version found in each header
  - the family tables follow ramp-ecosystem generate_ramp_variant.py: trajectory_module is a
    trajectory_type_t, pm_motor_parameter is not deep-copied, and the fixed-point FOC and
    120-degree families are supported
  - errors are reported through exit codes instead of SystemExit

Arguments (flags, same style as changeControlMethodRampProject.py):
  --config_xml            path to the project's configuration.xml
  --variant_model_header  path to ramp_variant_model.h
  --registry_header       path to ramp_instance_registry.h
  --out_dir               directory to write the generated files into
  --variant_template      path to the template of ramp_variant_config.h
  --registry_template     path to the template of ramp_instance_registry.c

Exit code = value of the last expression of this script (read by EaseScriptUtil):
  0  success
  1  generic failure
  6  skipped: neither header exists in the project
  7  schema version / enum symbol not supported by this generator
  8  header missing (only one of the two) or not parsable
  9  configuration.xml content not supported (unknown module family, shunt, ...)
"""

import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from string import Template

EXIT_SUCCESS = 0
EXIT_FAIL = 1
EXIT_SKIPPED_NO_VARIANT_HEADERS = 6
EXIT_SCHEMA_UNSUPPORTED = 7
EXIT_HEADER_INVALID = 8
EXIT_CONFIG_UNSUPPORTED = 9

GENERATOR_NAME = "QE for Motor"
# Keep the generated header free of machine specific absolute paths.
SOURCE_XML_NAME = "configuration.xml"

start_time = time.time()


class GenerationError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def get_arg(name):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------------------
# Header parsing (ramp_variant_model.h / ramp_instance_registry.h)
# --------------------------------------------------------------------------------------
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
_ENUM_RE = re.compile(
    r"typedef\s+enum\s*\{(?P<body>.*?)\}\s*(?P<name>[A-Za-z_]\w*)\s*;",
    re.DOTALL,
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_comments(text):
    text = _BLOCK_COMMENT_RE.sub("", text)
    return _LINE_COMMENT_RE.sub("", text)


def parse_enum_catalog(header_text):
    """type name -> set of enum symbol names (the *_MAX sentinels are skipped).

    Known limitation: a preprocessor directive placed *inside* an enum body is not
    handled (the entry following it is dropped). The current headers do not do that.
    """
    text = strip_comments(header_text)
    catalog = {}
    for match in _ENUM_RE.finditer(text):
        names = set()
        for item in match.group("body").split(","):
            name = item.strip().split("=", 1)[0].strip()
            if not name or name.endswith("_MAX") or not _IDENTIFIER_RE.match(name):
                continue
            names.add(name)
        catalog[match.group("name")] = names
    return catalog


def parse_define_int(header_text, macro, header_label):
    """Value of '#define <macro> (<n>U)' (parentheses and U suffix optional)."""
    text = strip_comments(header_text)
    pattern = (r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(macro)
               + r"[ \t]+\(?[ \t]*(\d+)[uU]?[ \t]*\)?")
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise GenerationError(EXIT_HEADER_INVALID,
                              "%s: could not find '#define %s <number>'" % (header_label, macro))
    return int(match.group(1))


# --------------------------------------------------------------------------------------
# Module-family mappings and enum registry (domain knowledge, same as the PoC)
# --------------------------------------------------------------------------------------
class FamilySpec(object):
    def __init__(self, enum, extended_cfg_type, deep_copy_pointer_members=(), user_cfg=True):
        self.enum = enum
        self.extended_cfg_type = extended_cfg_type
        # Algorithm families only: False when the family has no on-the-fly tuning. Such a motor gets
        # no user_cfg storage, no init/re-init copy and NULL *_user_cfg pointers in the registry.
        self.user_cfg = user_cfg
        # (field_name, pointee_type) pairs inside extended_cfg_type that are pointers to a
        # shared global; they must be deep-copied into a private per-motor global,
        # otherwise the "user_cfg" shadow would alias the original shared config.
        self.deep_copy_pointer_members = deep_copy_pointer_members


class EnumRegistry(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def build_enum_registry(catalog):
    """Resolve every enum symbol this generator can emit against the symbols found in the
    project's ramp_variant_model.h. A missing symbol fails here, before anything is written."""

    def enum(type_name, symbol):
        valid = catalog.get(type_name)
        if valid is None:
            raise GenerationError(
                EXIT_SCHEMA_UNSUPPORTED,
                "enum type '%s' not found in ramp_variant_model.h" % type_name)
        if symbol not in valid:
            raise GenerationError(
                EXIT_SCHEMA_UNSUPPORTED,
                "'%s' not found under enum type '%s' in ramp_variant_model.h (known values: %s)"
                % (symbol, type_name, ", ".join(sorted(valid))))
        return symbol

    return EnumRegistry(
        motor_family=enum("motor_family_t", "MOTOR_FAMILY_THREE_PHASE_BRUSHLESS"),
        control_mode=enum("control_mode_t", "CONTROL_MODE_SPEED"),
        module_enabled=enum("module_state_t", "MODULE_ENABLED"),
        module_disabled=enum("module_state_t", "MODULE_DISABLED"),
        sensor_disabled=enum("sensor_configuration_t", "SENSOR_CONFIGURATION_DISABLED"),
        trajectory_disabled=enum("trajectory_type_t", "TRAJECTORY_DISABLED"),
        algorithm_families={
            # pm_motor_parameter is deliberately NOT deep-copied: the position/speed modules and the
            # outer/inner control loops keep pointers to the original block captured when the
            # algorithm is opened (and ParameterUpdate does not refresh them), so a private copy
            # would leave them running on the FSP default motor after every parameter apply.
            "motor_pm_foc": FamilySpec(
                enum("control_algorithm_t", "CONTROL_ALGORITHM_FOC"),
                "motor_pm_foc_extended_cfg_t",
                deep_copy_pointer_members=(
                    ("pm_inner_design_parameter", "motor_design_param_t"),
                    ("pm_outer_design_parameter", "motor_outer_design_params_t"),
                ),
            ),
            # Fixed-point sibling (RA2T1): same variant enum (fixed vs float is a numerical
            # implementation concern, not a control_algorithm_t value), different outer type.
            "motor_pm_foc_fixed": FamilySpec(
                enum("control_algorithm_t", "CONTROL_ALGORITHM_FOC"),
                "motor_pm_foc_fixed_extended_cfg_t",
                deep_copy_pointer_members=(
                    ("pm_inner_design_parameter", "motor_design_param_t"),
                    ("pm_outer_design_parameter", "motor_outer_fixed_design_params_t"),
                ),
            ),
            # 120-degree: the algorithm enum comes straight from the algorithm module family. The
            # user_cfg part is kept (RAM copy of the extended cfg); the extended cfg holds only
            # values and instance pointers, so no deep copy is needed.
            "motor_120deg_hall": FamilySpec(
                enum("control_algorithm_t", "CONTROL_ALGORITHM_120DEG_HALL"),
                "motor_120deg_hall_fixed_extended_cfg_t",
            ),
            "motor_120deg_sensorless": FamilySpec(
                enum("control_algorithm_t", "CONTROL_ALGORITHM_120DEG_BEMF"),
                "motor_120deg_sensorless_fixed_extended_cfg_t",
            ),
        },
        sensor_families={
            "motor_sensor_bemf": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_BEMF_SENSORLESS"),
                "motor_sensor_bemf_extended_cfg_t"),
            "motor_sensor_bemf_fixed": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_BEMF_SENSORLESS"),
                "motor_sensor_bemf_fixed_extended_cfg_t"),
            "motor_sensor_hall": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_HALL_SENSOR"),
                "motor_sensor_hall_extended_cfg_t"),
            "motor_sensor_hall_fixed": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_HALL_SENSOR"),
                "motor_sensor_hall_fixed_extended_cfg_t"),
            "motor_sensor_bemf_hfi": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_HFI_BEMF_SENSORLESS"),
                "motor_sensor_bemf_hfi_extended_cfg_t"),
            "motor_sensor_afo": FamilySpec(
                enum("sensor_configuration_t", "SENSOR_CONFIGURATION_IPD_HFI_AFO_SENSORLESS"),
                "motor_sensor_afo_extended_cfg_t"),
        },
        hal_driver_families={
            "motor_hal_driver": FamilySpec(None, "motor_hal_driver_extended_cfg_t"),
        },
        tuner_families={
            "motor_tuner_pm": FamilySpec(None, "motor_tuner_pm_extend_cfg_t"),
        },
        trajectory_families={
            "motor_trajectory_linear": FamilySpec(
                enum("trajectory_type_t", "TRAJECTORY_LINEAR"),
                "motor_trajectory_linear_extended_cfg_t"),
        },
        current_sensing_map={
            "1_shunt": enum("current_sensing_t", "CURRENT_SENSING_ONE_SHUNT"),
            "2_shunt": enum("current_sensing_t", "CURRENT_SENSING_TWO_SHUNT"),
            "3_shunt": enum("current_sensing_t", "CURRENT_SENSING_THREE_SHUNT"),
        },
    )


# Generic (role level) user_cfg struct types and ramp_motor_instance_t field prefixes
# (from ramp_instance_registry.h).
GENERIC_CFG_TYPE = {
    "control": "motor_cfg_t",
    "algorithm": "motor_algorithm_cfg_t",
    "sensor": "motor_sensor_cfg_t",
    "hal_driver": "motor_hal_driver_cfg_t",
    "tuner": "motor_tuner_cfg_t",
    "trajectory": "motor_trajectory_cfg_t",
}

ROLE_FIELD_PREFIX = {
    "control": "p_motor_control",
    "algorithm": "p_motor_algo",
    "primary_sensor": "p_motor_primary_sensor",
    "auxiliary_sensor": "p_motor_auxiliary_sensor",
    "hal_driver": "p_motor_hal_driver",
    "tuner": "p_motor_tuner",
    "trajectory": "p_motor_trajectory",
}


def family_spec(role, table, family):
    try:
        return table[family]
    except KeyError:
        raise GenerationError(
            EXIT_CONFIG_UNSUPPORTED,
            "Unknown %s module family '%s'. Add its enum / extended-cfg-type mapping to the "
            "matching *_families table in build_enum_registry()." % (role, family))


# --------------------------------------------------------------------------------------
# configuration.xml parsing
# --------------------------------------------------------------------------------------
# Instance ids carry a trailing ".<random-int>" appended by e2studio.
_INSTANCE_SUFFIX = re.compile(r"\.\d+$")


def module_type_id(instance_id):
    return _INSTANCE_SUFFIX.sub("", instance_id)


def module_family(type_id):
    """Text after the last '_on_', e.g. 'motor_pm_foc'."""
    return type_id.rsplit("_on_", 1)[-1]


class ModuleRef(object):
    def __init__(self, name, type_id, family, properties):
        self.name = name  # e.g. "g_motor0_pm_foc" (from the '.name' property)
        self.type_id = type_id
        self.family = family
        self.properties = properties

    @property
    def stem(self):
        """Symbol name without the leading 'g_'."""
        return self.name[2:] if self.name.startswith("g_") else self.name

    @property
    def is_fixed(self):
        """Fixed-point instance, recognised by the '_fixed' suffix of its instance name."""
        return self.name.endswith("_fixed")


class MotorVariant(object):
    def __init__(self, motor_id, control, algorithm, hal_driver, primary_sensor,
                 auxiliary_sensor, tuner, trajectory, trajectory_active):
        self.motor_id = motor_id
        self.control = control
        self.algorithm = algorithm
        self.hal_driver = hal_driver
        self.primary_sensor = primary_sensor
        self.auxiliary_sensor = auxiliary_sensor
        self.tuner = tuner
        self.trajectory = trajectory
        # trajectory is "present" (Instance Registry) and may additionally be "active"
        # (Variant Manifest): see trajectory_user_mode_enabled().
        self.trajectory_active = trajectory_active


def _properties(module_element):
    return dict((p.get("id", ""), p.get("value", "")) for p in module_element.findall("property"))


def _module_name(instance_id, properties):
    for property_id, value in properties.items():
        if property_id.endswith(".name") and value:
            return value
    raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                          "module %s has no '.name' property; cannot derive a symbol name" % instance_id)


def _build_ref(instance_id, properties_by_id):
    properties = properties_by_id.get(instance_id, {})
    type_id = module_type_id(instance_id)
    return ModuleRef(_module_name(instance_id, properties), type_id, module_family(type_id), properties)


def _find_child(stack, requires_suffix):
    """Direct <stack> child wired via requires="...<requires_suffix>" (matched by suffix)."""
    for child in stack.findall("stack"):
        if child.get("requires", "").endswith(requires_suffix):
            return child
    return None


def trajectory_user_mode_enabled(trajectory):
    """Trajectory has a second gate besides being present in the stack tree: its own
    '.user_mode' property must end with '.true'. No other optional module has this."""
    key = next((k for k in trajectory.properties if k.endswith(".user_mode")), None)
    if key is None:
        raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                              "trajectory module %s: no '.user_mode' property found" % trajectory.name)
    return trajectory.properties[key].endswith(".true")


def discover_motors(root):
    """One MotorVariant per top-level motor_on_motor_control stack, in document order."""
    module_cfg = root.find("raModuleConfiguration")
    if module_cfg is None:
        raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                              "no <raModuleConfiguration> found - not an FSP configuration.xml?")

    properties_by_id = dict((m.get("id", ""), _properties(m)) for m in module_cfg.findall("module"))

    motors = []
    for context in module_cfg.findall("context"):
        for stack in context.findall("stack"):
            instance_id = stack.get("module", "")
            if module_type_id(instance_id) != "module.middleware.motor_on_motor_control":
                continue

            control = _build_ref(instance_id, properties_by_id)

            # algorithm and driver are mandatory, the other roles are optional.
            algo_stack = _find_child(stack, ".requires.algorithm")
            if algo_stack is None:
                raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                                      "motor instance %s: no algorithm module wired" % control.name)
            algorithm = _build_ref(algo_stack.get("module", ""), properties_by_id)

            driver_stack = _find_child(algo_stack, ".requires.driver")
            if driver_stack is None:
                raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                                      "motor instance %s: no HAL driver module wired" % control.name)
            hal_driver = _build_ref(driver_stack.get("module", ""), properties_by_id)

            def optional_ref(parent, suffix):
                child = _find_child(parent, suffix)
                return _build_ref(child.get("module", ""), properties_by_id) if child is not None else None

            # trajectory hangs under the control stack, the others under the algorithm stack
            trajectory = optional_ref(stack, ".requires.trajectory")
            trajectory_active = trajectory is not None and trajectory_user_mode_enabled(trajectory)
            tuner = optional_ref(algo_stack, ".requires.tuner")
            primary_sensor = optional_ref(algo_stack, ".requires.sensor1")
            auxiliary_sensor = optional_ref(algo_stack, ".requires.sensor2")

            # Fixed and float implementations cannot be mixed within a motor.
            fixed_flags = [(ref.name, ref.is_fixed) for ref in (algorithm, primary_sensor, auxiliary_sensor)
                           if ref is not None]
            if len(set(flag for _name, flag in fixed_flags)) > 1:
                kinds = ", ".join("%s: %s" % (n, "fixed" if f else "float") for n, f in fixed_flags)
                raise GenerationError(
                    EXIT_CONFIG_UNSUPPORTED,
                    "motor instance %s: mixed fixed-point and floating-point modules (%s)" % (control.name, kinds))

            motors.append(MotorVariant(len(motors), control, algorithm, hal_driver, primary_sensor,
                                       auxiliary_sensor, tuner, trajectory, trajectory_active))

    if not motors:
        raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                              "no motor_on_motor_control instance found in configuration.xml")
    return motors


# --------------------------------------------------------------------------------------
# Enum / role resolution
# --------------------------------------------------------------------------------------
def _sensor_enum(sensor, enums):
    if sensor is None:
        return enums.sensor_disabled
    return family_spec("sensor", enums.sensor_families, sensor.family).enum


def _module_state(is_enabled, enums):
    return enums.module_enabled if is_enabled else enums.module_disabled


def _current_sensing(hal_driver, enums):
    key = next((k for k in hal_driver.properties if k.endswith(".shunt")), None)
    if key is None:
        raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                              "HAL driver %s: no '.shunt' property found" % hal_driver.name)
    token = hal_driver.properties[key].rsplit(".", 1)[-1]
    try:
        return enums.current_sensing_map[token]
    except KeyError:
        raise GenerationError(EXIT_CONFIG_UNSUPPORTED,
                              "HAL driver %s: unknown shunt topology '%s'" % (hal_driver.name, token))


def _trajectory_type(m, enums):
    """trajectory_type_t of a motor: the family's type when the module is active, else DISABLED.

    Active means present *and* user_mode enabled (see trajectory_user_mode_enabled()). A family
    missing from enums.trajectory_families fails loudly in family_spec().
    """
    if m.trajectory is None or not m.trajectory_active:
        return enums.trajectory_disabled
    spec = family_spec("trajectory", enums.trajectory_families, m.trajectory.family)
    if spec.enum is None:
        raise GenerationError(
            EXIT_CONFIG_UNSUPPORTED,
            "trajectory family '%s' has no trajectory_type_t enum in trajectory_families" % m.trajectory.family)
    return spec.enum


def _all_roles(m):
    """Every registry role in field order; None where the module is not selected."""
    return [
        ("control", m.control),
        ("algorithm", m.algorithm),
        ("primary_sensor", m.primary_sensor),
        ("auxiliary_sensor", m.auxiliary_sensor),
        ("hal_driver", m.hal_driver),
        ("tuner", m.tuner),
        ("trajectory", m.trajectory),
    ]


def _present_roles(m, enums):
    """(role, ref, generic_cfg_type, extended_cfg_type, deep_copy_pointer_members) for every
    selected module. Control has no extended-cfg handling (same as the PoC). Empty for motors
    whose algorithm family has user_cfg=False: no user_cfg part."""
    algo_spec = family_spec("algorithm", enums.algorithm_families, m.algorithm.family)
    if not algo_spec.user_cfg:
        return []
    roles = [("control", m.control, GENERIC_CFG_TYPE["control"], None, ())]

    def add(role, ref, generic_type, spec):
        roles.append((role, ref, generic_type, spec.extended_cfg_type, spec.deep_copy_pointer_members))

    add("algorithm", m.algorithm, GENERIC_CFG_TYPE["algorithm"], algo_spec)
    if m.primary_sensor is not None:
        add("primary_sensor", m.primary_sensor, GENERIC_CFG_TYPE["sensor"],
            family_spec("sensor", enums.sensor_families, m.primary_sensor.family))
    if m.auxiliary_sensor is not None:
        add("auxiliary_sensor", m.auxiliary_sensor, GENERIC_CFG_TYPE["sensor"],
            family_spec("sensor", enums.sensor_families, m.auxiliary_sensor.family))
    add("hal_driver", m.hal_driver, GENERIC_CFG_TYPE["hal_driver"],
        family_spec("hal_driver", enums.hal_driver_families, m.hal_driver.family))
    if m.tuner is not None:
        add("tuner", m.tuner, GENERIC_CFG_TYPE["tuner"],
            family_spec("tuner", enums.tuner_families, m.tuner.family))
    if m.trajectory is not None:
        add("trajectory", m.trajectory, GENERIC_CFG_TYPE["trajectory"],
            family_spec("trajectory", enums.trajectory_families, m.trajectory.family))
    return roles


# --------------------------------------------------------------------------------------
# Rendering: dynamic blocks
# --------------------------------------------------------------------------------------
def _cont(line, width=74):
    """Right-pad a line and append the backslash line-continuation used in #define bodies."""
    return "%s \\" % line.ljust(width)


def render_motor_variant_table(motors, enums):
    rows = []
    for i, m in enumerate(motors):
        control_algorithm = family_spec("algorithm", enums.algorithm_families, m.algorithm.family).enum
        row_lines = [
            "    X(%dU," % m.motor_id,
            "      %s," % enums.motor_family,
            "      %s," % control_algorithm,
            "      %s," % _sensor_enum(m.primary_sensor, enums),
            "      %s," % _sensor_enum(m.auxiliary_sensor, enums),
            "      %s," % _current_sensing(m.hal_driver, enums),
            "      %s," % _module_state(m.tuner is not None, enums),
            "      %s," % _trajectory_type(m, enums),
            "      %s)" % enums.control_mode,
        ]
        last_row = i == len(motors) - 1
        for j, line in enumerate(row_lines):
            last_line = last_row and j == len(row_lines) - 1
            rows.append(line if last_line else _cont(line))
    return "\n".join(rows)


def render_extern_decls(motors):
    """The top motor-control instance is not declared extern in hal_data.h."""
    return "\n".join("extern const motor_instance_t %s;" % m.control.name for m in motors)


def render_user_cfg_globals(motors, enums):
    lines = []
    for m in motors:
        for _role, ref, generic_type, extended_type, deep_copy_members in _present_roles(m, enums):
            lines.append("%s g_user_%s_cfg;" % (generic_type.ljust(32), ref.stem))
            if extended_type:
                lines.append("%s g_user_%s_extended_cfg;" % (extended_type.ljust(32), ref.stem))
            for field, pointee_type in deep_copy_members:
                lines.append("%s g_user_%s_%s;" % (pointee_type.ljust(32), ref.stem, field))
    return "\n".join(lines)


def _render_motor_cfg_copy_block(m, enums):
    """Per-motor copy-into-user-cfg block, shared by init and re_init."""
    role_groups = []
    for _role, ref, _generic_type, extended_type, deep_copy_members in _present_roles(m, enums):
        lines = ["    g_user_%s_cfg = *(%s.p_cfg);" % (ref.stem, ref.name)]
        if extended_type:
            lines.append("    g_user_%s_extended_cfg = *(%s *) g_user_%s_cfg.p_extend;"
                         % (ref.stem, extended_type, ref.stem))
            lines.append("    g_user_%s_cfg.p_extend = &g_user_%s_extended_cfg;" % (ref.stem, ref.stem))
            for field, _pointee_type in deep_copy_members:
                lines.append("    g_user_%s_%s = *g_user_%s_extended_cfg.%s;"
                             % (ref.stem, field, ref.stem, field))
                lines.append("    g_user_%s_extended_cfg.%s = &g_user_%s_%s;"
                             % (ref.stem, field, ref.stem, field))
        role_groups.append("\n".join(lines))
    return "\n\n".join(role_groups)


def render_init_body(motors, enums):
    blocks = [_render_motor_cfg_copy_block(m, enums) for m in motors]
    return "\n\n".join(b for b in blocks if b)


def render_re_init_body(motors, enums):
    cases = []
    for m in motors:
        block = _render_motor_cfg_copy_block(m, enums)
        if not block:  # no user_cfg part (e.g. 120-degree without tuning): falls through to default
            continue
        indented = "\n".join(("    " + line if line else line) for line in block.splitlines())
        cases.append("        case %dU:\n%s\n            break;" % (m.motor_id, indented))
    return "\n\n".join(cases)


def render_registry_entries(motors, enums):
    entries = []
    for m in motors:
        has_user_cfg = family_spec("algorithm", enums.algorithm_families, m.algorithm.family).user_cfg
        role_blocks = []
        for role, ref in _all_roles(m):
            prefix = ROLE_FIELD_PREFIX[role]
            if ref is None:
                role_blocks.append("\n".join([
                    "        .%s = NULL," % prefix.ljust(26),
                    "        .%s = NULL," % (prefix + "_ctrl").ljust(26),
                    "        .%s = NULL," % (prefix + "_user_cfg").ljust(26),
                ]))
            else:
                role_blocks.append("\n".join([
                    "        .%s = &%s," % (prefix.ljust(26), ref.name),
                    "        .%s = &%s_ctrl," % ((prefix + "_ctrl").ljust(26), ref.name),
                    "        .%s = %s" % ((prefix + "_user_cfg").ljust(26),
                                          ("&g_user_%s_cfg," % ref.stem) if has_user_cfg else "NULL,"),
                ]))
        entries.append("    {\n" + "\n\n".join(role_blocks) + "\n    },")
    return "\n".join(entries)


# --------------------------------------------------------------------------------------
# File templates: plain text files given by --variant_template and --registry_template,
# rendered with string.Template, so the C braces need no escaping. Placeholders are written
# ${name}; a literal '$' is not allowed. QE chooses the template matching the schema version
# of the project, so it must provide the placeholders the renderer below fills in.
# --------------------------------------------------------------------------------------
def load_template(path):
    if not os.path.isfile(path):
        raise GenerationError(EXIT_FAIL, "template not found: " + path)
    # text mode: CRLF in the file (e.g. git autocrlf) is read as LF
    with open(path, "r", encoding="utf-8") as f:
        return Template(f.read())


def render_variant_config_v1(template, motors, enums, variant_schema, registry_schema):
    return template.substitute(
        generator=GENERATOR_NAME,
        source_xml=SOURCE_XML_NAME,
        schema_major=variant_schema[0],
        schema_minor=variant_schema[1],
        registry_schema_major=registry_schema[0],
        registry_schema_minor=registry_schema[1],
        motor_count=len(motors),
        motor_variant_table=render_motor_variant_table(motors, enums))


def render_instance_registry_v1(template, motors, enums):
    return template.substitute(
        generator=GENERATOR_NAME,
        source_xml=SOURCE_XML_NAME,
        extern_decls=render_extern_decls(motors),
        user_cfg_globals=render_user_cfg_globals(motors, enums),
        registry_entries=render_registry_entries(motors, enums),
        init_body=render_init_body(motors, enums),
        re_init_body=render_re_init_body(motors, enums))


# Renderer tables, keyed by the schema MAJOR version read from the project's headers.
# "schema" is the (major, minor) the renderer was written for; it is what ends up in the
# GEN_*_SCHEMA_VERSION_* macros.
VARIANT_RENDERERS = {1: {"schema": (1, 0),
                         "render": render_variant_config_v1}}
REGISTRY_RENDERERS = {1: {"schema": (1, 0),
                          "render": render_instance_registry_v1}}


# --------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------
def write_files_atomically(out_dir, contents):
    """Write every file to '<name>.tmp' first, then rename them into place, so a failure
    never leaves a half written pair behind."""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    tmp_paths = {}
    try:
        for name, content in contents.items():
            tmp_path = os.path.join(out_dir, name + ".tmp")
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            tmp_paths[name] = tmp_path
        for name, tmp_path in tmp_paths.items():
            os.replace(tmp_path, os.path.join(out_dir, name))
            print("[INFO] Wrote " + os.path.join(out_dir, name))
    finally:
        for tmp_path in tmp_paths.values():
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main():
    config_xml = get_arg("--config_xml")
    variant_header = get_arg("--variant_model_header")
    registry_header = get_arg("--registry_header")
    out_dir = get_arg("--out_dir")
    variant_template_path = get_arg("--variant_template")
    registry_template_path = get_arg("--registry_template")
    for flag, value in (("--config_xml", config_xml), ("--variant_model_header", variant_header),
                        ("--registry_header", registry_header), ("--out_dir", out_dir),
                        ("--variant_template", variant_template_path),
                        ("--registry_template", registry_template_path)):
        if not value:
            raise GenerationError(EXIT_FAIL, "argument %s is missing" % flag)

    if not os.path.isfile(config_xml):
        raise GenerationError(EXIT_FAIL, "configuration.xml not found: " + config_xml)

    has_variant_header = os.path.isfile(variant_header)
    has_registry_header = os.path.isfile(registry_header)
    if not has_variant_header and not has_registry_header:
        print("[INFO] ramp_variant_model.h and ramp_instance_registry.h were not found in the "
              "project; variant code generation is skipped.")
        return EXIT_SKIPPED_NO_VARIANT_HEADERS
    if has_variant_header != has_registry_header:
        missing = registry_header if has_variant_header else variant_header
        raise GenerationError(EXIT_HEADER_INVALID, "header not found: " + missing)

    variant_text = read_text(variant_header)
    registry_text = read_text(registry_header)
    catalog = parse_enum_catalog(variant_text)
    variant_major = parse_define_int(variant_text, "APPL_VARIANT_SCHEMA_VERSION_MAJOR",
                                     "ramp_variant_model.h")
    variant_minor = parse_define_int(variant_text, "APPL_VARIANT_SCHEMA_VERSION_MINOR",
                                     "ramp_variant_model.h")
    registry_major = parse_define_int(registry_text, "APPL_INSTANCE_REGISTRY_SCHEMA_VERSION_MAJOR",
                                      "ramp_instance_registry.h")
    registry_minor = parse_define_int(registry_text, "APPL_INSTANCE_REGISTRY_SCHEMA_VERSION_MINOR",
                                      "ramp_instance_registry.h")
    print("[INFO] Firmware schema: variant model %d.%d, instance registry %d.%d"
          % (variant_major, variant_minor, registry_major, registry_minor))

    # Both contracts must be supported, otherwise nothing is generated.
    unsupported = []
    if variant_major not in VARIANT_RENDERERS:
        unsupported.append("variant model major %d (supported: %s)"
                           % (variant_major, ", ".join(str(k) for k in sorted(VARIANT_RENDERERS))))
    if registry_major not in REGISTRY_RENDERERS:
        unsupported.append("instance registry major %d (supported: %s)"
                           % (registry_major, ", ".join(str(k) for k in sorted(REGISTRY_RENDERERS))))
    if unsupported:
        raise GenerationError(EXIT_SCHEMA_UNSUPPORTED,
                              "schema version not supported by this generator: " + "; ".join(unsupported))

    enums = build_enum_registry(catalog)

    try:
        root = ET.parse(config_xml).getroot()
    except ET.ParseError as e:
        raise GenerationError(EXIT_FAIL, "cannot parse configuration.xml: %s" % e)
    motors = discover_motors(root)
    print("[INFO] Discovered %d motor instance(s): %s"
          % (len(motors), ", ".join(m.control.name for m in motors)))

    variant_entry = VARIANT_RENDERERS[variant_major]
    registry_entry = REGISTRY_RENDERERS[registry_major]
    variant_template = load_template(variant_template_path)
    registry_template = load_template(registry_template_path)
    contents = {
        "ramp_variant_config.h": variant_entry["render"](variant_template, motors, enums,
                                                         variant_entry["schema"], registry_entry["schema"]),
        "ramp_instance_registry.c": registry_entry["render"](registry_template, motors, enums),
    }
    write_files_atomically(out_dir, contents)
    return EXIT_SUCCESS


try:
    exit_code = main()
except GenerationError as e:
    print("[ERROR] " + e.message)
    exit_code = e.code
except Exception as e:
    print("[ERROR] Unexpected failure: %s" % e)
    traceback.print_exc()
    exit_code = EXIT_FAIL
print("Execution time: %ss" % (time.time() - start_time))
exit_code