import ctypes
import math
import os.path
from dataclasses import dataclass
from enum import Enum
from typing import *

import lumberjack
import reference_parser
import utilities
from reference_parser import UnsupportedTypeError, FunctionReference

ScalarValue = Union[int, float, bool, str]
ArrayValue = Union[str, list[ScalarValue]]
SomeValue = Union[ScalarValue, ArrayValue]
AnyValue = Union[SomeValue, None]

ScalarCType = Union[ctypes.c_int, ctypes.c_float, ctypes.c_double, ctypes.c_char, ctypes.c_double]


class FunctionRunError(Exception):
    pass


class Primitive(Enum):
    Int = reference_parser.CType("int", 0)
    Float = reference_parser.CType("float", 0)
    Double = reference_parser.CType("double", 0)
    Char = reference_parser.CType("char", 0)
    Bool = reference_parser.CType("bool", 0)

    @staticmethod
    def get(c_type: str):
        primitive: Primitive
        for primitive in Primitive:
            if c_type == primitive.value.contents:
                return primitive

        raise reference_parser.UnsupportedTypeError(c_type)

    def native_type(self):
        if self == Primitive.Int:
            return int
        elif self == Primitive.Float:
            return float
        elif self == Primitive.Double:
            return float
        elif self == Primitive.Char:
            return str
        elif self == Primitive.Bool:
            return bool

    def foreign_type(self):
        if self == Primitive.Int:
            return ctypes.c_int
        elif self == Primitive.Float:
            return ctypes.c_float
        elif self == Primitive.Double:
            return ctypes.c_double
        elif self == Primitive.Char:
            return ctypes.c_char
        elif self == Primitive.Bool:
            return ctypes.c_bool

    @property
    def name(self) -> str:
        return self.value.contents


class TypeMismatchError(Exception):
    pass


class CScalar:
    primitive: Primitive
    value: Optional[SomeValue] = None

    def __init__(self, primitive: Primitive, value: Optional[ScalarValue] = None):
        self.primitive = primitive

        if value is not None and not isinstance(value, primitive.native_type()):
            raise TypeMismatchError(f"{value} is not a {primitive.name}")

        self.value = value

    def foreign_value(self, **kwargs):
        assert self.value is not None
        return self.primitive.foreign_type()(self.value)

    def values_match(self, o_type: Primitive, o_val) -> bool:
        try:
            # if o_val is a c_types value
            o_val = o_val.value
        except AttributeError:
            # if o_val is a native value
            pass

        other = Primitive(o_type, o_val)

        return self == other

    def __eq__(self, other):
        if self is other:
            return True

        if not isinstance(other, CScalar):
            return False

        if self.primitive != other.primitive:
            return False

        if self.value is None or other.value is None:
            return False

        if self.primitive == Primitive.Double or self.primitive == Primitive.Float:
            if math.isnan(self.value) and math.isnan(other.value):
                return True

        return self.value == other.value

    def c_repr(self, with_val: bool) -> str:
        if not with_val:
            return self.primitive.name
        else:
            raise NotImplementedError("sorry")

class CArray:
    def __init__(self, primitive: Primitive, value: Optional[ArrayValue] = None):
        self.primitive = primitive

        if value is None:
            self.val = None
        else:
            self.value = value

    @property
    def value(self):
        return self.val

    @value.setter
    def value(self, value: ArrayValue):
        assert value is not None

        if not all(isinstance(val, self.primitive.native_type()) for val in value):
            raise TypeMismatchError(f"{value} is not an array of {self.primitive.name}")

        self.val = value

    def foreign_value(self, length: Optional[int] = None, output: bool = False):
        assert self.value is not None
        assert not (length is None and output)

        if length is None:
            length = len(self.value)

        # ensures array is large enough
        assert length >= len(self.value)

        if self.primitive == Primitive.Char:
            # MARK: explicitly grows array to fit a string
            if length <= len(self.value):
                length = len(self.value) + 1

            self.value: str
            s = self.value.encode("ASCII")

            if output:
                return ctypes.create_string_buffer(s, length)
            else:
                c_str = ctypes.c_char_p(length)
                c_str.value = s

                return c_str
        else:
            arr_type = self.primitive.foreign_type() * length
            return arr_type(*self.value)

    def values_match(self, o_type: Primitive, o_val) -> bool:
        if o_type != self.primitive:
            return False

        try:
            # assumes it's a string
            # NOTE: this will only check equality up to the first '\0' character in o_val
            o_vals = o_val.value
        except AttributeError:
            # assumes it's an array (either native or foreign)
            o_vals = o_val[:]

        other = Primitive(o_type, o_vals)

        return self == other

    def __eq__(self, other):
        if self is other:
            return True

        if not isinstance(other, CArray):
            return False

        if self.primitive != other.primitive:
            return False

        if self.value is None or other.value is None:
            return False

        if len(self.value) != len(other.value):
            return False

        prim = self.primitive
        return all(CScalar(prim, v1) == CScalar(prim, v2) for v1, v2 in zip(self.value, other.value))

    def c_repr(self, with_val: bool) -> str:
        if not with_val:
            return f"{self.primitive.name}*"
        else:
            raise NotImplementedError("sorry")

    def unpack(self, val) -> ArrayValue:
        assert val is not None

        if self.primitive == Primitive.Char:
            return val.value
        else:
            return val[:]


class CVoid:
    def __eq__(self, other):
        return isinstance(other, CVoid)


CType = Union[CScalar, CArray]


@dataclass
class CParameter:
    name: str
    is_output: bool
    size: Optional[reference_parser.ParamSize]
    contents: CType
    ref: Optional = None  # reference to the ctypes value

    def is_array(self) -> bool:
        return isinstance(self.contents, CArray)

    def eval_size(self, values: dict) -> Optional[int]:
        if self.size is None:
            return None

        assert self.name == self.size.array

        var = self.size.var

        assert var in values

        return values.get(var)

    def c_repr(self, with_val: bool) -> str:
        if not with_val:
            return f"{self.contents.c_repr(with_val)} {self.name}"
        else:
            raise NotImplementedError("sorry")


class Function:
    """
    An executable version of a C function
    """

    def __init__(self, reference: FunctionReference, lib_path: str):
        if not (lib_path.startswith("./") or lib_path.startswith("/")):
            lib_path = f"./{lib_path}"

        exe = getattr(ctypes.CDLL(lib_path), reference.name)

        self.name = reference.name

        self.parameters = []

        for parameter in reference.parameters:
            if parameter.type.pointer_level == 0:
                contents = CScalar(Primitive.get(parameter.type.contents))
            elif parameter.type.pointer_level == 1:
                contents = CArray(Primitive.get(parameter.type.contents))
            else:
                raise UnsupportedTypeError("multi-level pointer")

            self.parameters.append(CParameter(parameter.name,
                                              reference.info.is_output(parameter),
                                              reference.info.size(parameter),
                                              contents))

        self.type = reference.type

        self.add_return_type(exe, reference.type)

        self.exe = exe

    def run(self, params: dict[str, SomeValue]):
        args = []

        for param in self.parameters:
            param.contents.value = params[param.name]
            val = param.contents.foreign_value(length=param.eval_size(params),
                                               output=param.is_output)

            param.ref = val
            args.append(val)

        try:
            return self.exe(*args)
        except ctypes.ArgumentError:
            raise FunctionRunError("could not build function types")

    def outputs(self):
        """
        Get the values of the output parameters

        NOTE: this does not check the function has been run, so only use it after calling the function.

        :return: names and values of all output parameters
        """
        return {param.name: param.contents.unpack(param.ref) for param in self.parameters if param.is_output}

    @staticmethod
    def add_return_type(exe, ret_type: reference_parser.CType):
        """
        Specifies the return type of a C function

        :param exe: the Function object to give a return type to
        :param ret_type: the representation of its type
        """
        if ret_type.contents == "void":
            exe.restype = None
            return

        prim = Primitive.get(ret_type.contents)

        if ret_type.pointer_level == 0:
            exe.restype = prim.foreign_type()
        elif ret_type.pointer_level == 1:
            exe.restype = CArray(prim)
            raise UnsupportedTypeError("return pointers")
        else:
            raise UnsupportedTypeError("multi-level return pointers")

    def safe_parameters(self) -> list[CParameter]:
        scalars = [param for param in self.parameters if isinstance(param.contents, CScalar)]
        arrays = [param for param in self.parameters if isinstance(param.contents, CArray)]

        return scalars + arrays


class CompilationError(Exception):
    def __init__(self, compilable: str, library: str):
        self.compilable = compilable
        self.library = library

    def __str__(self):
        return f"issue compiling {self.compilable} into {self.library}"


def compile_lib(path_to_compilable: str, lib_path: str):
    """
    Compile a reference to a usable version

    This usable version is a shared object, or a dynamic library.
    This version is designed for Linux, for macOS change the -soname to -install_name.
    Not sure how to support Windows, but there will probably be much bigger changes somewhere else too.

    :param path_to_compilable: a function to compile, can be .c or .s
    :param lib_path: the .so file to compile into
    """
    stdout, stderr = utilities.run_command(
        f"gcc -Wall -O0 -shared -fPIC -Wl,-install_name,{lib_path} -o {lib_path} {path_to_compilable}")

    if stderr:
        lumberjack.getLogger("error").error(stderr)
        raise CompilationError(path_to_compilable, lib_path)


def create(path_to_reference: str, path_to_compilable: str = None, lib_path: str = None) -> Function:
    """
    Helper to generate an executable

    :param path_to_reference: the reference directory
    :param path_to_compilable: the path to the version of the reference to compile,
    if :code:`None` then use "ref.c" in the reference directory
    :param lib_path: the .so file to compile into, generates random if not given
    :return:
    """
    if path_to_compilable is None:
        ref_file = "ref.c"
        path_to_compilable = os.path.join(path_to_reference, ref_file)

    ref = reference_parser.load_reference(path_to_reference)

    return create_from(ref, path_to_compilable, lib_path)


def create_from(reference: FunctionReference, path_to_compilable: str, lib_path: str = None) -> Function:
    if lib_path is None:
        if not os.path.exists("_tmp"):
            os.makedirs("_tmp")
        lib_path = os.path.join("_tmp", f"{utilities.get_tmp_path()}.so")

    compile_lib(path_to_compilable, lib_path)

    return Function(reference, lib_path)
