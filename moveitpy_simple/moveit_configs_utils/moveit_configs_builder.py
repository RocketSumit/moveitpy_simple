"""Simplify loading moveit config parameters.

This module provides builder-pattern based class to simplify loading moveit related parameters found in
ROBOT_moveit_config package generated by moveit setup assistant.

This package is a successor to moveit_configs_utils package. It is designed to be used to be more flexible and easier to use.
Main differences are:
    - No implicit loading of default parameters. All parameters must be explicitly loaded by calling the corresponding function.
        - This is to avoid loading parameters that are not needed, sometimes only a few parameters are needed.
        - If you want to load all the parameters, you can use the load_all function.
    - Package name has to be explicitly provided. Previously it was implicitly loaded by appending _moveit_config to the robot name.
    - Does not require the moveit config package to be installed. It can be used to load parameters from the path to the package. -- TODO(Jafar)
    - No longer autoloads the values from the URDF and SRDF tags from .setup_assistant file.
    - Remove all the complicated logic for loading moveit controller manager, planning pipeline, and urdf/srdf parameters.
    - Support loading moveit_config.toml file for default values. -- TODO(Jafar)
    - Add support for loading parameters for servo. -- TODO(Jafar)

moveit_config.toml

```toml
[moveit_configs]
robot_description = "config/my_robot.urdf.xacro"
robot_description_semantic = "config/my_robot.srdf.xacro"
robot_description_kinematics = "config/kinematics.yaml"
planning_pipelines.ompl = "config/ompl_planning.yaml"
planning_pipelines.chomp = "config/chomp_planning.yaml"
planning_pipelines.trajopt = "config/trajopt_planning.yaml"
planning_pipelines.pilz = "config/pilz_planning.yaml"
planning_pipelines.stomp = "config/stomp_planning.yaml"
planning_pipelines.CUSTOM_PLANNER = "config/PLANNER.yaml"
trajectory_execution = "config/trajectory_execution.yaml"
sensors = "config/sensors_3d.yaml"
joint_limits = "config/joint_limits.yaml"
moveit_cpp = "config/moveit_cpp.yaml"
# TODO(Jafar): Support this
# move_group = ""

[robot_description]
mapping1 = "value1"
mapping2 = "value2"

[robot_description_semantic]
mapping1 = "value1"
mapping2 = "value2"

[robot_description_kinematics]
mapping1 = "value1"
mapping2 = "value2"

# Applies to all planning pipelines
[planning_pipelines]
mapping1 = "value1"
mapping2 = "value2"

[planning_pipelines.ompl]
mapping1 = "value1"
mapping2 = "value2"

[planning_pipelines.chomp]
mapping1 = "value1"
mapping2 = "value2"

[trajectory_execution]
mapping1 = "value1"
mapping2 = "value2"

[sensors]
mapping1 = "value1"
mapping2 = "value2"

[joint_limits]
mapping1 = "value1"
mapping2 = "value2"

[moveit_cpp]
mapping1 = "value1"
mapping2 = "value2"

# TODO(Jafar): Support this
# [move_group]
# mapping1 = "asd"
# mapping2 = "asd"
```

Each function in MoveItConfigsBuilder has a file_path as an argument which is used to override the default
path for the file

Example:
    moveit_configs = MoveItConfigsBuilder("robot_name")
                    # Relative to robot_name_moveit_configs
                    .robot_description_semantic(Path("my_config") / "my_file.srdf")
                    .to_moveit_configs()
    # Or
    moveit_configs = MoveItConfigsBuilder("robot_name")
                    # Absolute path to robot_name_moveit_config
                    .robot_description_semantic(Path.home() / "my_config" / "new_file.srdf")
                    .to_moveit_configs()
"""

import contextlib
import logging
from dataclasses import InitVar, dataclass, field
from enum import Enum
from pathlib import Path

import ament_index_python.packages as ament_packages
import toml
from ament_index_python.packages import get_package_share_directory
from launch.some_substitutions_type import SomeSubstitutionsType
from launch_ros.parameter_descriptions import ParameterValue
from moveit_configs_utils.substitutions import Xacro

from moveitpy_simple.moveit_configs_utils.file_loaders import (
    load_xacro,
    load_yaml,
    raise_if_file_not_found,
)

LOGGER = logging.getLogger(__name__)
COLOR_YELLOW = "\x1b[33;20m"
COLOR_RESET = "\x1b[0m"
logging.basicConfig(level=logging.INFO)


def normalize_path_value(value: str) -> Path:
    """Normalize a package value, convert paths starting with package:// to absolute path.

    Args:
        value: Package name or path to package

    Returns:
        Package name
    """
    if isinstance(value, str) and value.startswith("package://"):
        package_name, relative_path = value.split("package://")[1].split("/", 1)
        return get_package_path(package_name) / relative_path
    return value


class PackageNotFoundError(KeyError):
    """Raised when a package is not found."""


def get_full_path(path: str | Path) -> Path:
    """Get the full path to a file/directory.

    Args:
        path: Path to a file/directory

    Returns:
        Full path to the file/directory
    """
    if isinstance(path, str):
        try:
            full_path = get_package_share_directory(path)
        except ament_packages.PackageNotFoundError as e:
            msg = f"Path to a package {path} not found"
            raise PackageNotFoundError(msg) from e
        return Path(full_path)
    if isinstance(path, Path):
        if path.exists():
            return path
        msg = f"Path {path} not found"
        raise PackageNotFoundError(msg)
    return None


def get_package_path(package: str | Path) -> Path:
    """Get the full path to a package."""
    package_path = get_full_path(package)
    return package_path.parent if package_path.is_file() else package_path


def load_moveit_configs_toml(file_path: Path) -> dict:
    """Load moveit_configs from a toml file.

    Args:
        file_path: Path to the file that contains moveit_configs

    Returns:
        Loaded configs or an empty dict if moveit_configs.toml doesn't exists
    """
    if file_path.is_file() or (file_path := file_path / "moveit_configs.toml").exists():
        return toml.load(file_path)
    return {}


def get_missing_configs(configs: dict) -> list[str]:
    """Get missing configs from a dictionary.

    Args:
        configs: Input configs loaded from a toml file

    Returns:
        Return a list of missing sections (Doesn't include the extend key)
    """
    missing_configs = [
        section
        for section in ConfigSections
        if configs.get(ConfigSections.MOVEIT_CONFIGS, {}).get(section) is None
    ]
    missing_configs.remove(ConfigSections.MOVEIT_CONFIGS)
    with contextlib.suppress(ValueError):
        missing_configs.remove(ConfigSections.EXTEND)

    return missing_configs


def extend_configs(package_path: Path, configs: dict) -> dict:
    """Extend package_path's moveit_configs with another package.

    Args:
        package_path: Path to the package that contains the moveit_configs.toml file
        configs: The configs loaded from the moveit_configs.toml file

    Returns:
        Extended configs if moveit_configs.toml contains an extend key
    """
    if (
        len(missing_sections := get_missing_configs(configs)) == 0
        or (
            base_package := configs.get(ConfigSections.MOVEIT_CONFIGS, {}).get(
                ConfigSections.EXTEND,
            )
        )
        is None
    ):
        return configs
    base_config_path = (
        package_path / base_package
        if (package_path / base_package).exists()
        else base_package
    )
    base_package = get_package_path(base_config_path)
    base_package_configs = load_moveit_configs_toml(get_full_path(base_config_path))
    extended_configs = configs.copy()
    extended_moveit_configs = extended_configs[ConfigSections.MOVEIT_CONFIGS]
    extended_moveit_configs.pop(ConfigSections.EXTEND)
    missing_sections.append(ConfigSections.EXTEND)
    for missing_section in missing_sections:
        if (
            missing_section_value := base_package_configs.get(
                ConfigSections.MOVEIT_CONFIGS,
                {},
            ).get(missing_section)
        ) is not None:
            if isinstance(missing_section_value, Path | str):
                extended_moveit_configs[
                    missing_section
                ] = base_package / normalize_path_value(missing_section_value)
            elif isinstance(missing_section_value, dict):
                resolved_missing_section_value = {
                    key: base_package / normalize_path_value(value)
                    if isinstance(value, Path | str)
                    else normalize_path_value(value)
                    for key, value in missing_section_value.items()
                }
                extended_moveit_configs[
                    missing_section
                ] = resolved_missing_section_value
            else:
                msg = f"Invalid type for {missing_section} in {base_package} moveit_configs.toml"
                raise TypeError(
                    msg,
                )
            extended_configs[missing_section] = base_package_configs.get(
                missing_section,
            )

    return extend_configs(
        base_package,
        extended_configs,
    )


class ConfigSections(str, Enum):
    """Contains the standard sections in moveit_configs.toml."""

    EXTEND = "extend"
    MOVEIT_CONFIGS = "moveit_configs"
    ROBOT_DESCRIPTION = "robot_description"
    ROBOT_DESCRIPTION_SEMANTIC = "robot_description_semantic"
    SENSORS = "sensors"
    MOVEIT_CPP = "moveit_cpp"
    ROBOT_DESCRIPTION_KINEMATICS = "robot_description_kinematics"
    JOINT_LIMITS = "joint_limits"
    TRAJECTORY_EXECUTION = "trajectory_execution"
    PLANNING_PIPELINES = "planning_pipelines"
    PILZ_CARTESIAN_LIMITS = "pilz_cartesian_limits"


@dataclass(slots=True)
class ConfigEntry:
    """A class that contains a path to a config file and a dictionary of mappings."""

    path: Path
    mappings: dict


@dataclass(slots=True)
class PlanningPipelinesConfigEntry:
    """A class that contains the configs to the planning pipelines configs."""

    pipelines: list[str]
    configs: list[ConfigEntry]
    default_planning_pipeline: str


@dataclass(slots=True)
class MoveItConfigs:
    """Class containing MoveIt related parameters."""

    # A pathlib Path to the moveit config package
    package_path: str | None = None
    # A dictionary that has the contents of the URDF file.
    robot_description: dict = field(default_factory=dict)
    # A dictionary that has the contents of the SRDF file.
    robot_description_semantic: dict = field(default_factory=dict)
    # A dictionary IK solver specific parameters.
    robot_description_kinematics: dict = field(default_factory=dict)
    # A dictionary that contains the planning pipelines parameters.
    planning_pipelines: dict = field(default_factory=dict)
    # A dictionary contains parameters for trajectory execution & moveit controller managers.
    trajectory_execution: dict = field(default_factory=dict)
    # A dictionary that has the planning scene monitor's parameters.
    planning_scene_monitor: dict = field(default_factory=dict)
    # A dictionary that has the sensor 3d configuration parameters.
    sensors_3d: dict = field(default_factory=dict)
    # A dictionary containing move_group's non-default capabilities.
    move_group_capabilities: dict = field(default_factory=dict)
    # A dictionary containing the overridden position/velocity/acceleration limits.
    joint_limits: dict = field(default_factory=dict)
    # A dictionary containing MoveItCpp related parameters.
    moveit_cpp: dict = field(default_factory=dict)
    # A dictionary containing the cartesian limits for the Pilz planner.
    pilz_cartesian_limits: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Merge all the parameters in a dict."""
        parameters = {}
        parameters |= self.robot_description
        parameters.update(self.robot_description_semantic)
        parameters.update(self.robot_description_kinematics)
        parameters.update(self.planning_pipelines)
        parameters.update(self.trajectory_execution)
        parameters.update(self.planning_scene_monitor)
        parameters.update(self.sensors_3d)
        parameters.update(self.joint_limits)
        parameters.update(self.moveit_cpp)
        # Update robot_description_planning with pilz cartesian limits
        if self.pilz_cartesian_limits:
            if parameters.get("robot_description_planning") is not None:
                parameters["robot_description_planning"].update(
                    self.pilz_cartesian_limits,
                )
            else:
                parameters["robot_description_planning"] = self.pilz_cartesian_limits
        return parameters


@dataclass(slots=True)
class MoveItConfigsBuilder:
    """A class that implements the builder pattern to create moveit configs from a package or a path."""

    package: InitVar[Path | str] = None
    package_path: Path | None = None
    _robot_description_config: ConfigEntry | None = None
    _robot_description_semantic_config: ConfigEntry | None = None
    _robot_description_kinematics_config: ConfigEntry | None = None
    _planning_pipelines_config: PlanningPipelinesConfigEntry | None = None
    _trajectory_execution_config: ConfigEntry | None = None
    _sensors_config: ConfigEntry | None = None
    _pilz_cartesian_limits_config: ConfigEntry | None = None
    _joint_limits_config: ConfigEntry | None = None
    _moveit_cpp_config: ConfigEntry | None = None
    _default_configs: dict = field(default_factory=dict)

    def __post_init__(self, package: Path | str) -> None:
        """Constructor.

        Args:
            package: The package name or path to the package.
        """
        moveit_configs_path = get_full_path(package)
        self.package_path = get_package_path(package)
        self._default_configs = extend_configs(
            self.package_path,
            load_moveit_configs_toml(moveit_configs_path),
        )

    def _make_config_entry_from_file(
        self,
        file_path: Path,
        mappings: dict | None = None,
    ) -> ConfigEntry:
        """Create a ConfigEntry from a file.

        Args:
            file_path: The path to the file.
            mappings: Mappings to be applied to the file.

        Returns:
            ConfigEntry: A ConfigEntry object.
        """
        raise_if_file_not_found(file_path)
        return ConfigEntry(
            path=file_path,
            mappings=mappings or {},
        )

    def _make_config_entry_from_section(
        self,
        section: ConfigSections,
        option: str | None = None,
    ) -> ConfigEntry:
        """Make a ConfigEntry from a section in the moveit_configs.toml file.

        Args:
            section: Section name in the moveit_configs.toml file.
            option: A key in the section.

        Returns:
            ConfigEntry: A ConfigEntry object.
        """
        if not self._default_configs:
            msg = f"Default configs are not loaded. Please provide a moveit_configs.toml file, or explicitly pass the file_path when loading MoveItConfigsBuilder('...').{section}(file_path='...')."
            raise RuntimeError(
                msg,
            )

        if (
            moveit_configs := self._default_configs.get(ConfigSections.MOVEIT_CONFIGS)
        ) is None:
            msg = "No [moveit_configs] section found in moveit_configs.toml"
            raise RuntimeError(
                msg,
            )

        if (value := moveit_configs.get(section)) is None:
            msg = f"No value {section} found for [moveit_configs] section in moveit_configs.toml"
            raise RuntimeError(
                msg,
            )

        if option and (value := value.get(option)) is None:
            msg = f"No value {section}.{option} found for [moveit_configs] section in moveit_configs.toml"
            raise RuntimeError(
                msg,
            )

        return ConfigEntry(
            path=self.package_path / normalize_path_value(value),
            # Note we do XXX.get(...) or {} on purpose, we might have a section with a None value
            mappings=(self._default_configs.get(section) or {}).get(option, {})
            if option
            else self._default_configs.get(section, {}),
        )

    def robot_description(
        self,
        file_path: str | None = None,
        mappings: dict[SomeSubstitutionsType, SomeSubstitutionsType] | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load robot description.

        Args:
            file_path: Absolute or relative path to the URDF file (w.r.t. robot_name_moveit_config).
            mappings: mappings to be passed when loading the xacro file.

        Returns:
            Instance of MoveItConfigsBuilder with robot_description loaded.
        """
        if file_path:
            self._robot_description_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._robot_description_config = self._make_config_entry_from_section(
                ConfigSections.ROBOT_DESCRIPTION,
            )

        return self

    def robot_description_semantic(
        self,
        file_path: str | None = None,
        mappings: dict[SomeSubstitutionsType, SomeSubstitutionsType] | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load semantic robot description.

        Args:
            file_path: Absolute or relative path to the SRDF file (w.r.t. robot_name_moveit_config).
            mappings: mappings to be passed when loading the xacro file.

        Returns:
            Instance of MoveItConfigsBuilder with robot_description_semantic loaded.
        """
        if file_path:
            self._robot_description_semantic_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._robot_description_semantic_config = (
                self._make_config_entry_from_section(
                    ConfigSections.ROBOT_DESCRIPTION_SEMANTIC,
                )
            )

        return self

    def robot_description_kinematics(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load IK solver parameters.

        Args:
            file_path: Absolute or relative path to the kinematics yaml file (w.r.t. robot_name_moveit_config).
            mappings: mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with robot_description_kinematics loaded.
        """
        if file_path:
            self._robot_description_kinematics_config = (
                self._make_config_entry_from_file(
                    self.package_path / file_path,
                    mappings,
                )
            )
        else:
            self._robot_description_kinematics_config = (
                self._make_config_entry_from_section(
                    ConfigSections.ROBOT_DESCRIPTION_KINEMATICS,
                )
            )

        return self

    def joint_limits(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load joint limits overrides.

        Args:
            file_path: Absolute or relative path to the joint limits yaml file (w.r.t. robot_name_moveit_config).
            mappings: mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with robot_description_planning loaded.
        """
        if file_path:
            self._joint_limits_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._joint_limits_config = self._make_config_entry_from_section(
                ConfigSections.JOINT_LIMITS,
            )

        return self

    def moveit_cpp(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load MoveItCpp parameters.

        Args:
            file_path: Absolute or relative path to the MoveItCpp yaml file (w.r.t. robot_name_moveit_config).
            mappings: mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with moveit_cpp loaded.
        """
        if file_path:
            self._moveit_cpp_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._moveit_cpp_config = self._make_config_entry_from_section(
                ConfigSections.MOVEIT_CPP,
            )
        return self

    def trajectory_execution(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load trajectory execution and moveit controller managers' parameters.

        Args:
            file_path: Absolute or relative path to the controllers yaml file (w.r.t. robot_name_moveit_config).
            mappings: Mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with trajectory_execution loaded.
        """
        if file_path:
            self._trajectory_execution_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._trajectory_execution_config = self._make_config_entry_from_section(
                ConfigSections.TRAJECTORY_EXECUTION,
            )

        return self

    # TODO(Jafar): This's only for move_group move to a separate config file
    # def planning_scene_monitor(
    #     self,
    # ):
    #     moveit_configs.planning_scene_monitor = {
    #         # TODO: Fix parameter namespace upstream -- see planning_scene_monitor.cpp:262

    def sensors(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load sensors_3d parameters.

        Args:
            file_path: Absolute or relative path to the sensors_3d yaml file (w.r.t. robot_name_moveit_config).
            mappings: Mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with robot_description_planning loaded.
        """
        if file_path:
            self._sensors_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._sensors_config = self._make_config_entry_from_section(
                ConfigSections.SENSORS,
            )
        return self

    def planning_pipelines(
        self,
        pipelines: list[str] | None = None,
        default_planning_pipeline: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load planning pipelines parameters.

        Args:
            pipelines: List of the planning pipelines to be loaded.
            default_planning_pipeline: Name of the default planning pipeline.
            mappings: Mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with planning_pipelines loaded.
        """
        if pipelines is not None:
            planning_pipelines_configs = [
                self._make_config_entry_from_file(
                    self.package_path / "config" / f"{pipeline}_planning.yaml",
                    mappings,
                )
                for pipeline in pipelines
            ]
        else:
            pipelines = list(
                self._default_configs.get(ConfigSections.MOVEIT_CONFIGS, {})
                .get(ConfigSections.PLANNING_PIPELINES, {})
                .keys(),
            )
            planning_pipelines_configs = [
                self._make_config_entry_from_section(
                    ConfigSections.PLANNING_PIPELINES,
                    planner,
                )
                for planner in pipelines
            ]

        # Define default pipeline as needed
        if not default_planning_pipeline and "ompl" in pipelines:
            default_planning_pipeline = "ompl"

        if default_planning_pipeline not in pipelines:
            msg = f"default_planning_pipeline: `{default_planning_pipeline}` doesn't name any of the input pipelines `{','.join(pipelines)}`"
            raise RuntimeError(
                msg,
            )

        self._planning_pipelines_config = PlanningPipelinesConfigEntry(
            pipelines=pipelines.copy(),
            default_planning_pipeline=default_planning_pipeline,
            configs=planning_pipelines_configs,
        )

        return self

    def pilz_cartesian_limits(
        self,
        file_path: str | None = None,
        mappings: dict | None = None,
    ) -> "MoveItConfigsBuilder":
        """Load pilz cartesian limits parameters.

        Args:
            file_path: Absolute or relative path to the pilz cartesian limits yaml file (w.r.t. robot_name_moveit_config).
            mappings: Mappings to be passed when loading the yaml file.

        Returns:
            Instance of MoveItConfigsBuilder with pilz cartesian limits loaded.
        """
        if file_path:
            self._pilz_cartesian_limits_config = self._make_config_entry_from_file(
                self.package_path / file_path,
                mappings,
            )
        else:
            self._pilz_cartesian_limits_config = self._make_config_entry_from_section(
                ConfigSections.PILZ_CARTESIAN_LIMITS,
            )
        return self

    def load_all(self) -> "MoveItConfigsBuilder":
        """Load all configs.

        Returns:
            Instance of MoveItConfigsBuilder with all configs loaded.
        """
        if not self._default_configs:
            LOGGER.warning(
                f"{COLOR_YELLOW}Request to load all configs, but no default configs found. Make sure to create moveit_configs.toml file.{COLOR_RESET}",
            )
        existing_configs = [
            section
            for section in ConfigSections
            if self._default_configs.get(ConfigSections.MOVEIT_CONFIGS, {}).get(section)
            is not None
        ]
        for config in existing_configs:
            match config:
                case ConfigSections.ROBOT_DESCRIPTION:
                    self.robot_description()
                case ConfigSections.ROBOT_DESCRIPTION_SEMANTIC:
                    self.robot_description_semantic()
                case ConfigSections.SENSORS:
                    self.sensors()
                case ConfigSections.MOVEIT_CPP:
                    self.moveit_cpp()
                case ConfigSections.ROBOT_DESCRIPTION_KINEMATICS:
                    self.robot_description_kinematics()
                case ConfigSections.JOINT_LIMITS:
                    self.joint_limits()
                case ConfigSections.TRAJECTORY_EXECUTION:
                    self.trajectory_execution()
                case ConfigSections.PLANNING_PIPELINES:
                    self.planning_pipelines()
                case ConfigSections.PILZ_CARTESIAN_LIMITS:
                    self.pilz_cartesian_limits()
        return self

    def to_moveit_configs(self) -> MoveItConfigs:  # noqa: C901, PLR0912
        """Get MoveIt configs from ROBOT_NAME_moveit_config.

        Returns:
            An MoveItConfigs instance with all parameters loaded.
        """
        moveit_configs = MoveItConfigs()
        if self._robot_description_config is not None:
            # If mappings is None or a dictionary of strings, load the xacro file as a string.
            # Otherwise, load it as a ParameterValue.
            # This makes it possible to use the builder with MoveItPy while still being able to
            # use a ros2 launch's substitution types.
            if (self._robot_description_config.mappings is None) or all(
                (isinstance(key, str) and isinstance(value, str))
                for key, value in self._robot_description_config.mappings.items()
            ):
                moveit_configs.robot_description = {
                    "robot_description": load_xacro(
                        self._robot_description_config.path,
                        mappings=self._robot_description_config.mappings,
                    ),
                }
            else:
                moveit_configs.robot_description = {
                    "robot_description": ParameterValue(
                        Xacro(
                            str(self._robot_description_config.path),
                            mappings=self._robot_description_config.mappings,
                        ),
                        value_type=str,
                    ),
                }

        if self._robot_description_semantic_config is not None:
            # Support both MoveItPy and ros2 launch substitution types similar to robot_description
            if (self._robot_description_semantic_config.mappings is None) or all(
                (isinstance(key, str) and isinstance(value, str))
                for key, value in self._robot_description_semantic_config.mappings.items()
            ):
                moveit_configs.robot_description_semantic = {
                    "robot_description_semantic": load_xacro(
                        self._robot_description_semantic_config.path,
                        mappings=self._robot_description_semantic_config.mappings,
                    ),
                }
            else:
                moveit_configs.robot_description_semantic = {
                    "robot_description_semantic": ParameterValue(
                        Xacro(
                            str(self._robot_description_semantic_config.path),
                            mappings=self._robot_description_semantic_config.mappings,
                        ),
                        value_type=str,
                    ),
                }

        if self._robot_description_kinematics_config is not None:
            moveit_configs.robot_description_kinematics = {
                "robot_description_kinematics": load_yaml(
                    self._robot_description_kinematics_config.path,
                    mappings=self._robot_description_kinematics_config.mappings,
                ),
            }

        if self._planning_pipelines_config:
            moveit_configs.planning_pipelines = {
                "planning_pipelines.pipeline_names": self._planning_pipelines_config.pipelines,
                "default_planning_pipeline": self._planning_pipelines_config.default_planning_pipeline,
            }
            for pipeline, pipeline_config in zip(
                self._planning_pipelines_config.pipelines,
                self._planning_pipelines_config.configs,
                strict=True,
            ):
                moveit_configs.planning_pipelines[pipeline] = load_yaml(
                    file_path=pipeline_config.path,
                    mappings=pipeline_config.mappings,
                )

        if self._trajectory_execution_config is not None:
            moveit_configs.trajectory_execution = load_yaml(
                self._trajectory_execution_config.path,
                mappings=self._trajectory_execution_config.mappings,
            )

        if self._sensors_config is not None:
            moveit_configs.sensors_3d = load_yaml(
                self._sensors_config.path,
                mappings=self._sensors_config.mappings,
            )

        if self._joint_limits_config is not None:
            moveit_configs.joint_limits = {
                "robot_description_planning": load_yaml(
                    self._joint_limits_config.path,
                    mappings=self._joint_limits_config.mappings,
                ),
            }

        if self._moveit_cpp_config is not None:
            moveit_configs.moveit_cpp = load_yaml(
                self._moveit_cpp_config.path,
                mappings=self._moveit_cpp_config.mappings,
            )

        # if not moveit_configs.planning_scene_monitor:
        if self._pilz_cartesian_limits_config is not None:
            moveit_configs.pilz_cartesian_limits = load_yaml(
                self._pilz_cartesian_limits_config.path,
                mappings=self._pilz_cartesian_limits_config.mappings,
            )
        return moveit_configs
