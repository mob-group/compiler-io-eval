import ctypes
from typing import *
import os.path
from reference_parser import FunctionReference, CType, ParseIssue

ScalarValue = Union[int, float, bool, str]
ArrayValue = Union[str, list[ScalarValue]]
SomeValue = Union[ScalarValue, ArrayValue]
AnyValue = Union[SomeValue, None]

ScalarCType = Union[ctypes.c_int, ctypes.c_float, ctypes.c_double, ctypes.c_char, ctypes.c_double]


class CArray:
    scalar_type = None
    output = None
    refs = None

    @classmethod
    def from_param(cls, obj: ArrayValue):
        if cls.scalar_type == ctypes.c_char:
            s = bytes(obj, "ASCII")

            if cls.output:
                raise Exception("output strings are not supported")
                #return ctypes.create_string_buffer(s)
            else:
                return ctypes.c_char_p(s)

        arr_type = cls.scalar_type * len(obj)
        val = arr_type(*obj)

        # this is kinda gross
        if cls.output:
            cls.refs.append(val)

        return val


class Parameter:
    def __init__(self, name: str, c_type: CType, is_output: bool, refs: list):
        self.name = name
        self.is_output = is_output

        if c_type.pointer_level == 0:
            self.c_type = self.get_scalar_type(c_type.contents)
        elif c_type.pointer_level == 1:
            scalar_type = self.get_scalar_type(c_type.contents)
            self.c_type = type(f"{c_type.contents.capitalize()}Array",
                               (CArray,),
                               { "scalar_type": scalar_type,
                                 "output": is_output,
                                 "refs": refs})
        else:
            raise Exception("multi-level pointers are not supported")

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
    def __init__(self, reference: str, path: str):
        reference_path = os.path.join(path, reference)

        ref = FunctionReference.parse(reference_path)
        if (issues := ref.validate() - ParseIssue.ignorable()):
            raise Exception(f"bad input: {'; '.join(issue.value for issue in issues)}")

        exe = getattr(ctypes.CDLL("mega_ref.so"), reference)

        self.out_vals = []

        self.parameters = tuple(Parameter(param.name, param.type, ref.info.is_output(param), self.out_vals)
                                for param in ref.parameters())

        self.add_return_type(exe, ref.type)
        exe.argtypes = (param.c_type for param in self.parameters)

        self.exe = exe

    def run(self, **params):
        args = [params[param.name] for param in self.parameters]

        return self.exe(*args)

    def outputs(self):
        out_params = [param.name for param in self.parameters if param.is_output]

        return { param: val for param, val in zip(out_params, self.out_vals) }

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
    prog = "matmul"

    func = Function(prog, path)

    ret = func.run(a=[1, 2, 3, 4, 5], b=[10, 9, 8, 7, 6], n=5)

    print(f"{ret} :: {[(name, val[:]) for name, val in func.outputs().items()]}")
