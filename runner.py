import ctypes
from typing import *
import os.path

import reference_parser
from reference_parser import FunctionReference, CType, ParseIssue

ScalarValue = Union[int, float, bool, str]
ArrayValue = Union[str, list[ScalarValue]]
SomeValue = Union[ScalarValue, ArrayValue]
AnyValue = Union[SomeValue, None]

ScalarCType = Union[ctypes.c_int, ctypes.c_float, ctypes.c_double, ctypes.c_char, ctypes.c_double]


class CArray:
    parameter = None
    scalar_type = None
    output = None

    @classmethod
    def from_param(cls, obj: ArrayValue):
        if cls.scalar_type == ctypes.c_char:
            s = obj.encode("ASCII")

            if cls.output:
                raise Exception("output strings are not supported")
                # return ctypes.create_string_buffer(s)
            else:
                return ctypes.c_char_p(s)

        arr_type = cls.scalar_type * len(obj)
        val = arr_type(*obj)

        if cls.output:
            # TODO: edit this to use proper setter function
            cls.parameter.set_value(val)

        return val


class Parameter:
    def __init__(self, name: str, c_type: CType, is_output: bool,):
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
            raise Exception("multi-level pointers are not supported")

    @property
    def value(self) -> SomeValue:
        assert self.is_output

        try:
            # value is a string
            return self.output_val.value.decode()
        except Exception:
            # value is a normal array
            return self.output_val[:]

    # for some reason the @value.setter wasn't working
    def set_value(self, value: ctypes.Array):
        self.output_val = value

    @staticmethod
    def get_scalar_type(c_type_name: str):
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
            raise Exception("check void types separately, there isn't a ctypes representation")
        else:
            raise Exception(f"type {c_type_name} is not supported")


class Function:
    def __init__(self, reference: FunctionReference, lib_path: str):
        exe = getattr(ctypes.CDLL(lib_path), reference.name)

        self.parameters = tuple(Parameter(param.name, param.type, reference.info.is_output(param))
                                for param in reference.parameters())

        self.add_return_type(exe, reference.type)
        exe.argtypes = (param.c_type for param in self.parameters)

        self.exe = exe

    def run(self, **params):
        args = [params[param.name] for param in self.parameters]

        return self.exe(*args)

    def outputs(self):
        return {param.name: param.value for param in self.parameters if param.is_output}

    @staticmethod
    def add_return_type(exe, ret_type):
        if ret_type.contents == "void":
            exe.restype = None
        elif ret_type.pointer_level == 0:
            exe.restype = Parameter.get_scalar_type(ret_type.contents)
        elif ret_type.pointer_level == 1:
            exe.restype = ctypes.POINTER(Parameter.get_scalar_type(ret_type.contents))
            raise Exception("return pointers are not supported")
        else:
            raise Exception("multi-level return pointers are not supported")


if __name__ == '__main__':
    path = "/Users/sami/Documents/haxx/internship/synthesis-eval/examples/"
    prog = "subeq"

    ref = reference_parser.FunctionReference.parse(os.path.join(path, prog))
    func = Function(ref, "mega_ref.so")

    ret = func.run(a=[1, 2, 3, 4, 5], b=[10, 9, 8, 7, 6], n=5)

    print(f"{ret} :: {[(name, val[:]) for name, val in func.outputs().items()]}")
