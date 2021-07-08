import ctypes
import os.path
import sys
from typing import *
import reference_parser
import utilities
from reference_parser import FunctionReference, CType, UnsupportedTypeError

ScalarValue = Union[int, float, bool, str]
ArrayValue = Union[str, list[ScalarValue]]
SomeValue = Union[ScalarValue, ArrayValue]
AnyValue = Union[SomeValue, None]

ScalarCType = Union[ctypes.c_int, ctypes.c_float, ctypes.c_double, ctypes.c_char, ctypes.c_double]


class FunctionRunError(Exception):
    pass


class CArray:
    """
    A general type to store ctypes arrays, c_char_p, and string buffers
    """
    parameter = None
    scalar_type = None
    output = None

    @classmethod
    def from_param(cls, obj: ArrayValue):
        """
        Implementing the ctypes method

        This method allows native Python arrays/strings to be converted into usable C ones
        when a C function is called.
        These types are registered in the functions :code:`argtypes` and the rest is handled automatically.

        Since this is called automatically, and the C versions of objects are created here,
        it is the only place where references to C objects can be stored.
        They are only required for output parameters, so here they are registered to the parameter.

        :param obj: the native Python value to convert
        :return: the C version of that value
        """
        if cls.scalar_type == ctypes.c_char:
            s = obj.encode("ASCII")

            if cls.output:
                raise UnsupportedTypeError("output strings")
                # return ctypes.create_string_buffer(s)
            else:
                return ctypes.c_char_p(s)

        arr_type = cls.scalar_type * len(obj)
        val = arr_type(*obj)

        if cls.output:
            # TODO: edit this to use proper setter function
            cls.parameter.set_value(val)

        return val


class InvalidTypeError(Exception):
    pass


class Parameter:
    """
    A wrapper for a parameter to a function
    """
    def __init__(self, name: str, c_type: CType, is_output: bool):
        self.name = name
        self.is_output = is_output
        self.output_val = None

        if c_type.pointer_level == 0:
            self.c_type = self.get_scalar_type(c_type.contents)
        elif c_type.pointer_level == 1:
            scalar_type = self.get_scalar_type(c_type.contents)
            self.c_type = type(f"{c_type.contents.capitalize()}Array_{self.name}",
                               (CArray,),
                               {"scalar_type": scalar_type,
                                "output": is_output,
                                "parameter": self,
                                })
        else:
            raise UnsupportedTypeError("multi-level pointers")

    @property
    def value(self) -> SomeValue:
        """
        Retrieves the value for this parameter

        Only works for output parameters, as this is designed to be used to grab the result value.

        :return: the value of the parameter
        """
        assert self.is_output

        try:
            # value is a string
            return self.output_val.value.decode()
        except AttributeError:
            # value is a normal array
            return self.output_val[:]

    # for some reason the @value.setter wasn't working
    def set_value(self, value: ctypes.Array):
        """
        Setter for the parameters value

        :param value: the new value to keep
        """
        self.output_val = value

    @staticmethod
    def get_scalar_type(c_type_name: str):
        """
        Converts the name of a type into the corresponding ctypes class

        :param c_type_name: the name of the C type
        :return: the class that matches
        """
        if c_type_name == "int":
            return ctypes.c_int
        elif c_type_name == "float":
            return ctypes.c_float
        elif c_type_name == "double":
            return ctypes.c_double
        elif c_type_name == "char":
            return ctypes.c_char
        elif c_type_name == "bool":
            return ctypes.c_bool
        elif c_type_name == "void":
            raise InvalidTypeError("check void types separately, there isn't a ctypes representation")
        else:
            raise UnsupportedTypeError(c_type_name)


class Function:
    """
    An executable version of a C function
    """
    def __init__(self, reference: FunctionReference, lib_path: str):
        if not (lib_path.startswith("./") or lib_path.startswith("/")):
            lib_path = f"./{lib_path}"

        exe = getattr(ctypes.CDLL(lib_path), reference.name)

        self.parameters = tuple(Parameter(param.name, param.type, reference.info.is_output(param))
                                for param in reference.parameters())

        self.add_return_type(exe, reference.type)
        exe.argtypes = (param.c_type for param in self.parameters)

        self.exe = exe

    def run(self, **params):
        """
        Run the function with the given parameters

        :param params: parameter values, matching those given in the reference
        :return: the return value of the function call
        """
        args = [params[param.name] for param in self.parameters]

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
        return {param.name: param.value for param in self.parameters if param.is_output}

    @staticmethod
    def add_return_type(exe, ret_type: CType):
        """
        Specifies the return type of a C function

        :param exe: the Function object to give a return type to
        :param ret_type: the representation of its type
        """
        if ret_type.contents == "void":
            exe.restype = None
        elif ret_type.pointer_level == 0:
            exe.restype = Parameter.get_scalar_type(ret_type.contents)
        elif ret_type.pointer_level == 1:
            exe.restype = ctypes.POINTER(Parameter.get_scalar_type(ret_type.contents))
            raise UnsupportedTypeError("return pointers")
        else:
            raise UnsupportedTypeError("multi-level return pointers")


class CompilationError(Exception):
    def __init__(self, compilable: str, library: str):
        self.compilable = compilable
        self.library = compilable

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
        f"gcc -Wall -O0 -shared -fPIC -Wl,-soname,{lib_path} -o {lib_path} {path_to_compilable}")

    if stderr:
        print(stderr, file=sys.stderr)
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
        lib_path = f"{utilities.get_tmp_path()}.so"

    compile_lib(path_to_compilable, lib_path)

    return Function(reference, lib_path)
