import inspect
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Optional, TypeVar

from qtpy.QtCore import QObject, Signal
from qtpy.QtWidgets import QWidget

from . import style as style_module
from .base import Serializable
from .enums import ConnectionPolicy, NodeValidationState, PortType
from .port import Port

NodeDataType = namedtuple("NodeDataType", ("id", "name"))


V = TypeVar("V")


class NodeData:
    """
    Class represents data transferred between nodes.

    The actual data is stored in subtypes
    """

    data_type = NodeDataType(None, None)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.data_type is None:
            raise ValueError("Subclasses must set the `data_type` attribute")

    def same_type(self, other) -> bool:
        """
        Is another NodeData instance of the same type?

        Parameters
        ----------
        other : NodeData

        Returns
        -------
        value : bool
        """
        return self.data_type.id == other.data_type.id


@dataclass(frozen=True)
class PortCount:
    inputs: int = 0
    outputs: int = 0

    def __getitem__(self, type: PortType):
        if type == PortType.input:
            return self.inputs
        if type == PortType.output:
            return self.outputs
        raise ValueError


@dataclass(frozen=True)
class CaptionOverride:
    inputs: dict[int, str] | None = None
    outputs: dict[int, str] | None = None


@dataclass(frozen=True)
class DataTypes:
    inputs: dict[int, NodeDataType]
    outputs: dict[int, NodeDataType]


class NodeDataModel(Serializable, QObject):
    name: str | None = None
    caption: str | None = None
    caption_visible = True
    num_ports = PortCount(1, 1)

    caption_override: CaptionOverride | None = None

    _port_caption: dict[PortType, dict[int, str]] | None = None
    port_caption_visible: dict[PortType, dict[int, bool]]

    all_data_types: NodeDataType | None = None
    data_types: DataTypes | None = None
    _data_type: dict

    # data_updated and data_invalidated refer to the port index that has
    # changed:
    data_updated = Signal(int)
    data_invalidated = Signal(int)

    computing_started = Signal()
    computing_finished = Signal()
    embedded_widget_size_updated = Signal()

    def __init__(self, style=None, parent=None):
        super().__init__(parent=parent)
        if style is None:
            style = style_module.default_style
        self._style = style

    def __init_subclass__(cls, verify=True, **kwargs):
        super().__init_subclass__(**kwargs)
        # For all subclasses, if no name is defined, default to the class name
        if cls.name is None:
            cls.name = cls.__name__
        if cls.caption is None and cls.caption_visible:
            cls.caption = cls.name

        num_ports = cls.num_ports
        if isinstance(num_ports, property):
            # Dynamically defined - that's OK, but we can't verify it.
            return

        if verify:
            cls._verify()

    @classmethod
    def _verify(cls):
        """
        Verify the data model won't crash in strange spots
        Ensure valid dictionaries:
            - num_ports
            - data_type
            - port_caption
            - port_caption_visible
        """
        num_ports = cls.num_ports
        if isinstance(num_ports, property):
            # Dynamically defined - that's OK, but we can't verify it.
            return

        # TODO while the end result is nicer, this is ugly; refactor away...
        def new_dict(value: V) -> dict[PortType, dict[int, V]]:
            return {
                PortType.input: {i: value for i in range(num_ports[PortType.input])},
                PortType.output: {i: value for i in range(num_ports[PortType.output])},
            }

        def get_default(attr, default, valid_type):
            current = getattr(cls, attr, None)
            if current is None:
                # Unset - use the default
                return default

            if valid_type is not None and isinstance(current, valid_type):
                # Fill in the dictionary with the user-provided value
                return current

            if attr == "data_type" and inspect.isclass(current) and issubclass(current, NodeData):
                return current.data_type

            if inspect.ismethod(current) or inspect.isfunction(current):
                raise ValueError(
                    f"{attr} should not be a function; saw: {current}\nDid you forget a @property decorator?"
                )

            try:
                type(default)(current)
            except TypeError:
                raise ValueError(f"{attr} is of an unexpected type: {current}") from None

            # Fill in the dictionary with the given value
            return current

        def fill_defaults(attr, default, valid_type=None):
            if isinstance(getattr(cls, attr, None), dict):
                return

            default = get_default(attr, default, valid_type)
            if default is None:
                raise ValueError(f"Cannot leave {attr} unspecified")

            setattr(cls, attr, new_dict(default))

        cls._port_caption = new_dict("")
        if cls.caption_override is not None:
            # captions = {}
            if cls.caption_override.inputs is not None:
                cls._port_caption[PortType.input] = cls.caption_override.inputs

            if cls.caption_override.outputs is not None:
                cls._port_caption[PortType.output] = cls.caption_override.outputs

        all_types = cls.all_data_types
        data_types = cls.data_types
        if all_types is not None and data_types is not None:
            raise ValueError(f"Cannot use all_types and data_types simultaneously in {cls.__name__}")
        assert all_types or data_types, f"either all_types or data_types should be used in {cls.__name__}"

        if all_types is not None:
            cls._data_type = new_dict(all_types)
        if data_types is not None:
            cls._data_type = {PortType.input: data_types.inputs, PortType.output: data_types.outputs}

        fill_defaults("port_caption_visible", False)
        reasons = []
        for attr in ("_data_type", "_port_caption", "port_caption_visible"):
            try:
                dct = getattr(cls, attr)
            except AttributeError:
                reasons.append(f"{cls.__name__} is missing dictionary: {attr}")
                continue

            if isinstance(dct, property):
                continue

            for port_type in {PortType.input, PortType.output}:
                if port_type not in dct:
                    if num_ports[port_type] == 0:
                        dct[port_type] = {}
                    else:
                        reasons.append(f"Port type key {attr}[{port_type!r}] missing")
                        continue

                reasons.extend(
                    f"Port key {attr}[{port_type!r}][{i}] missing"
                    for i in range(num_ports[port_type])
                    if i not in dct[port_type]
                )

        if reasons:
            reason_text = "\n".join(f"* {reason}" for reason in reasons)
            raise ValueError(f"Verification of NodeDataModel class failed:\n{reason_text}")

    @property
    def style(self):
        "Style collection for drawing this data model"
        return self._style

    @property
    def port_caption(self):
        return self._port_caption

    def save(self) -> dict:
        """
        Subclasses may implement this to save additional state for
        pickling/saving to JSON.

        Returns
        -------
        value : dict
        """
        return {}

    def restore(self, doc: dict):
        """
        Subclasses may implement this to load additional state from
        pickled or saved-to-JSON data.

        Parameters
        ----------
        value : dict
        """
        return {}

    def __setstate__(self, doc: dict):
        """
        Set the state of the NodeDataModel

        Parameters
        ----------
        doc : dict
        """
        self.restore(doc)
        return doc

    def __getstate__(self) -> dict:
        """
        Get the state of the NodeDataModel for saving/pickling

        Returns
        -------
        value : QJsonObject
        """
        doc = {"name": self.name}
        doc.update(**self.save())
        return doc

    @property
    def data_type(self):
        """
        Data type placeholder - to be implemented by subclass.

        Parameters
        ----------
        port_type : PortType
        port_index : int

        Returns
        -------
        value : NodeDataType
        """
        return self._data_type

    def port_out_connection_policy(self, port_index: int) -> ConnectionPolicy:
        """
        Port out connection policy

        Parameters
        ----------
        port_index : int

        Returns
        -------
        value : ConnectionPolicy
        """
        return ConnectionPolicy.many

    @property
    def node_style(self) -> style_module.NodeStyle:
        """
        Node style

        Returns
        -------
        value : NodeStyle
        """
        return self._style.node

    def set_in_data(self, node_data: NodeData | None, port: Port):
        """
        Triggers the algorithm; to be overridden by subclasses

        Parameters
        ----------
        node_data : NodeData
        port : Port
        """
        ...

    def out_data(self, port: int) -> NodeData | None:
        """
        Out data. If None is returned, then nothing will be propagated.

        Parameters
        ----------
        port : int

        Returns
        -------
        NodeData | None
        """
        ...

    def embedded_widget(self) -> QWidget:
        """
        Embedded widget

        Returns
        -------
        value : QWidget
        """
        ...

    def resizable(self) -> bool:
        """
        Resizable

        Returns
        -------
        value : bool
        """
        return False

    def validation_state(self) -> NodeValidationState:
        """
        Validation state

        Returns
        -------
        value : NodeValidationState
        """
        return NodeValidationState.valid

    def validation_message(self) -> str:
        """
        Validation message

        Returns
        -------
        value : str
        """
        return ""

    def painter_delegate(self):
        """
        Painter delegate

        Returns
        -------
        value : NodePainterDelegate
        """
        return

    def input_connection_created(self, connection):
        """
        Input connection created

        Parameters
        ----------
        connection : Connection
        """
        ...

    def input_connection_deleted(self, connection):
        """
        Input connection deleted

        Parameters
        ----------
        connection : Connection
        """
        ...

    def output_connection_created(self, connection):
        """
        Output connection created

        Parameters
        ----------
        connection : Connection
        """
        ...

    def output_connection_deleted(self, connection):
        """
        Output connection deleted

        Parameters
        ----------
        connection : Connection
        """
        ...
